import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from time import perf_counter

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import AudioAnalysis, Base, DraftReview, FusionPlan, Project, TextAnalysis
from app.services.audio_analysis import analyze_audio_for_audiobook
from app.services.semantic_graph import expand_term, graph_status, reason_term, upsert_relation
from app.services.semantic_store import get_lexicon, merge_lexicon, save_lexicon
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates
from app.services.fusion import build_fusion_plan
from app.services.llm import llm_enabled
from app.services.text_analysis import analyze_text_for_audiobook
from app.services.exporter import export_cue_csv, export_sfx_zip
from app.services.reasoning_observability import get_reason_cache, log_reason_event, set_reason_cache
from app.services.ops_draft import generate_lexicon_draft
from app.services.rollout import resolve_semantic_backend
from app.models import ReasoningLog

app = Flask(settings.app_name)
CORS(app)


def _fallback_audio_result_on_error(err: Exception) -> dict:
    return {
        'duration_sec': 180.0,
        'bpm': 120.0,
        'tags': ['电影感', '史诗感', '战斗推进'],
        'markers': [
            {'label': '起势段', 'time_sec': 9.0, 'type': 'build'},
            {'label': '高潮一', 'time_sec': 50.4, 'type': 'peak'},
            {'label': '回落一', 'time_sec': 68.4, 'type': 'valley'},
            {'label': '高潮二', 'time_sec': 111.6, 'type': 'peak'},
            {'label': '回落二', 'time_sec': 129.6, 'type': 'valley'},
            {'label': '终局高潮', 'time_sec': 147.6, 'type': 'peak'},
        ],
        'report_markdown': (
            '# 音乐分析报告（降级版）\n\n'
            '- 音频元数据解析异常，系统已自动降级为可执行分析。\n'
            '- 你仍可继续进行文本分析与融合执行单生成。\n'
            f'- 异常信息：{type(err).__name__}: {err}\n'
        ),
        'report_json': None,
        'analysis_mode': 'rules-only',
        'llm_structured': False,
        'llm_enabled': llm_enabled(),
        'report_mode': settings.report_mode_default,
        'effective_report_mode': None,
        'llm_fallback_applied': True,
        'llm_attempted_modes': [],
        'degraded': True,
    }


@app.before_request
def ensure_tables():
    Base.metadata.create_all(bind=engine)


@app.get('/health')
def health():
    key_tail = settings.llm_api_key[-4:] if settings.llm_api_key else ''
    return jsonify(
        {
            'ok': True,
            'env': settings.app_env,
            'llm_enabled': llm_enabled(),
            'llm_provider': settings.llm_provider,
            'llm_model': settings.llm_model,
            'llm_key_tail': key_tail,
            'semantic_backend': settings.semantic_backend,
        }
    )


@app.get(f'{settings.api_prefix}/semantic/expand')
def semantic_expand():
    term = (request.args.get('term') or '').strip()
    project_id = request.args.get('project_id', type=int)
    if not term:
        return jsonify({'detail': 'term is required'}), 400
    backend = resolve_semantic_backend(project_id=project_id, subject=term)
    ex = expand_term(term, backend_override=backend)
    return jsonify({'term': term, 'expanded_terms': sorted(ex.terms), 'sources': ex.sources, 'effective_backend': backend})


@app.get(f'{settings.api_prefix}/semantic/search-sfx')
def semantic_search_sfx():
    term = (request.args.get('term') or '').strip()
    project_id = request.args.get('project_id', type=int)
    top_n = int((request.args.get('top_n') or '5').strip())
    if not term:
        return jsonify({'detail': 'term is required'}), 400
    t0 = perf_counter()
    backend = resolve_semantic_backend(project_id=project_id, subject=term)
    library = load_sfx_library(backend_override=backend)
    matched = match_sfx_candidates(term, library, top_n=top_n, backend_override=backend)
    duration_ms = int((perf_counter() - t0) * 1000)

    with SessionLocal() as db:
        log_reason_event(
            db=db,
            event_type='search_sfx',
            term=term,
            backend=backend,
            project_id=project_id,
            req={'top_n': top_n},
            resp={'success': True, 'duration_ms': duration_ms, 'match_count': len(matched), 'matches': matched[:10]},
        )

    return jsonify({'term': term, 'top_n': top_n, 'matches': matched, 'effective_backend': backend, 'duration_ms': duration_ms})


@app.get(f'{settings.api_prefix}/graph/status')
def semantic_graph_status():
    return jsonify(graph_status())


@app.post(f'{settings.api_prefix}/graph/edge')
def semantic_graph_upsert_edge():
    payload = request.get_json(force=True)
    head = payload.get('head', '')
    relation = payload.get('relation', '')
    tail = payload.get('tail', '')
    bidirectional = bool(payload.get('bidirectional', False))

    try:
        result = upsert_relation(head=head, relation=relation, tail=tail, bidirectional=bidirectional)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'detail': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'detail': str(e)}), 400
    except Exception as e:
        return jsonify({'detail': f'graph upsert failed: {e}'}), 500


@app.get(f'{settings.api_prefix}/graph/reason')
def semantic_graph_reason():
    term = (request.args.get('term') or '').strip()
    project_id = request.args.get('project_id', type=int)
    limit = int((request.args.get('limit') or '12').strip())
    max_hops = int((request.args.get('max_hops') or str(settings.semantic_neo4j_depth)).strip())
    if not term:
        return jsonify({'detail': 'term is required'}), 400

    limit = max(1, min(50, limit))
    max_hops = max(1, min(4, max_hops))
    backend = resolve_semantic_backend(project_id=project_id, subject=term)

    t0 = perf_counter()
    with SessionLocal() as db:
        cached = get_reason_cache(db, term=term, backend=backend, limit=limit, max_hops=max_hops)
        if cached:
            cached['cache_hit'] = True
            cached['effective_backend'] = backend
            cached['duration_ms'] = int((perf_counter() - t0) * 1000)
            log_reason_event(
                db=db,
                event_type='graph_reason',
                term=term,
                backend=backend,
                project_id=project_id,
                req={'limit': limit, 'max_hops': max_hops, 'cache_hit': True},
                resp={'success': True, 'duration_ms': cached['duration_ms'], 'inferred_count': len(cached.get('inferred', []))},
            )
            return jsonify(cached)

        out = reason_term(term=term, limit=limit, max_hops=max_hops, backend_override=backend)
        out['cache_hit'] = False
        out['effective_backend'] = backend
        out['duration_ms'] = int((perf_counter() - t0) * 1000)

        set_reason_cache(db, term=term, backend=backend, limit=limit, max_hops=max_hops, result=out)
        log_reason_event(
            db=db,
            event_type='graph_reason',
            term=term,
            backend=backend,
            project_id=project_id,
            req={'limit': limit, 'max_hops': max_hops, 'cache_hit': False},
            resp={
                'success': True,
                'duration_ms': out['duration_ms'],
                'inferred_count': len(out.get('inferred', [])),
                'local_count': out.get('local_count'),
                'neo4j_count': out.get('neo4j_count'),
            },
        )

        return jsonify(out)


@app.get(f'{settings.api_prefix}/graph/metrics')
def semantic_graph_metrics():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        rows = db.execute(
            select(ReasoningLog.event_type, ReasoningLog.backend, ReasoningLog.output_json)
            .where(ReasoningLog.created_at >= cutoff)
        ).all()

    bucket = {}
    for event_type, backend, output_json in rows:
        key = (event_type, backend)
        b = bucket.setdefault(
            key,
            {
                'event_type': event_type,
                'backend': backend,
                'count': 0,
                'success_observed_count': 0,
                'success_count': 0,
                'duration_count': 0,
                'duration_sum_ms': 0,
            },
        )
        b['count'] += 1
        try:
            payload = json.loads(output_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload.get('success'), bool):
            b['success_observed_count'] += 1
            if payload.get('success') is True:
                b['success_count'] += 1
        dur = payload.get('duration_ms')
        if isinstance(dur, int) and dur >= 0:
            b['duration_count'] += 1
            b['duration_sum_ms'] += dur

    metrics = []
    for _, b in bucket.items():
        avg_ms = round(b['duration_sum_ms'] / b['duration_count'], 2) if b['duration_count'] else None
        success_rate = (
            round(b['success_count'] / b['success_observed_count'], 4) if b['success_observed_count'] else None
        )
        metrics.append(
            {
                'event_type': b['event_type'],
                'backend': b['backend'],
                'count': b['count'],
                'success_observed_count': b['success_observed_count'],
                'success_rate': success_rate,
                'avg_duration_ms': avg_ms,
            }
        )
    metrics.sort(key=lambda x: (x['event_type'], x['backend']))
    return jsonify({'days': days, 'metrics': metrics})


@app.get(f'{settings.api_prefix}/semantic/lexicon')
def semantic_get_lexicon():
    data = get_lexicon()
    return jsonify({'size': len(data), 'lexicon': data})


@app.put(f'{settings.api_prefix}/semantic/lexicon')
def semantic_replace_lexicon():
    payload = request.get_json(force=True)
    lexicon = payload.get('lexicon')
    if not isinstance(lexicon, dict):
        return jsonify({'detail': 'lexicon must be an object: {keyword:[synonyms...]}'}), 400
    save_lexicon(lexicon)
    data = get_lexicon()
    return jsonify({'ok': True, 'size': len(data), 'lexicon': data})


@app.patch(f'{settings.api_prefix}/semantic/lexicon')
def semantic_merge_lexicon():
    payload = request.get_json(force=True)
    delta = payload.get('lexicon')
    if not isinstance(delta, dict):
        return jsonify({'detail': 'lexicon must be an object: {keyword:[synonyms...]}'}), 400
    data = merge_lexicon(delta)
    return jsonify({'ok': True, 'size': len(data), 'lexicon': data})


@app.post(f'{settings.api_prefix}/projects')
def create_project():
    payload = request.get_json(force=True)
    title = (payload.get('title') or '').strip()
    if not title:
        return jsonify({'detail': 'title is required'}), 400

    with SessionLocal() as db:
        project = Project(title=title)
        db.add(project)
        db.commit()
        db.refresh(project)
        return jsonify({'id': project.id, 'title': project.title, 'created_at': project.created_at.isoformat()})


@app.get(f'{settings.api_prefix}/projects/<int:project_id>')
def get_project(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        return jsonify({'id': project.id, 'title': project.title, 'created_at': project.created_at.isoformat()})


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/audio')
def upload_audio(project_id: int):
    file = request.files.get('file')
    if file is None:
        return jsonify({'detail': 'file is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        upload_dir = Path(settings.upload_dir).resolve()
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(file.filename or 'audio.bin').suffix
        save_path = upload_dir / f'project_{project_id}{ext}'

        file_bytes = file.read()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            return jsonify({'detail': f'File too large. Max {settings.max_upload_mb}MB'}), 413
        save_path.write_bytes(file_bytes)

        report_mode = (request.args.get('report_mode') or settings.report_mode_default).strip().lower()
        try:
            result = analyze_audio_for_audiobook(str(save_path), report_mode=report_mode)
        except Exception as e:
            app.logger.exception('audio analyze failed; fallback enabled')
            result = _fallback_audio_result_on_error(e)

        row = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        if row is None:
            row = AudioAnalysis(
                project_id=project_id,
                file_name=file.filename or save_path.name,
                file_path=str(save_path),
                duration_sec=result['duration_sec'],
                bpm=result['bpm'],
                report_markdown=result['report_markdown'],
                markers_json=json.dumps(result['markers'], ensure_ascii=False),
                tags_json=json.dumps(result['tags'], ensure_ascii=False),
            )
            db.add(row)
        else:
            row.file_name = file.filename or save_path.name
            row.file_path = str(save_path)
            row.duration_sec = result['duration_sec']
            row.bpm = result['bpm']
            row.report_markdown = result['report_markdown']
            row.markers_json = json.dumps(result['markers'], ensure_ascii=False)
            row.tags_json = json.dumps(result['tags'], ensure_ascii=False)

        db.commit()
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/text')
def analyze_text(project_id: int):
    payload = request.get_json(force=True)
    text = (payload.get('text') or '').strip()
    report_mode = (payload.get('report_mode') or request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    if not text:
        return jsonify({'detail': 'text is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        audio_context = None
        if audio is not None:
            audio_context = {
                'duration_sec': audio.duration_sec,
                'bpm': audio.bpm,
                'tags': json.loads(audio.tags_json),
                'markers': json.loads(audio.markers_json),
                'report_markdown': audio.report_markdown,
            }

        result = analyze_text_for_audiobook(text, report_mode=report_mode, audio_context=audio_context)
        row = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()

        if row is None:
            row = TextAnalysis(
                project_id=project_id,
                raw_text=text,
                report_markdown=result['report_markdown'],
                scenes_json=json.dumps(result['scenes'], ensure_ascii=False),
            )
            db.add(row)
        else:
            row.raw_text = text
            row.report_markdown = result['report_markdown']
            row.scenes_json = json.dumps(result['scenes'], ensure_ascii=False)

        db.commit()
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/fusion')
def build_fusion(project_id: int):
    report_mode = (request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()

        if not audio or not text:
            missing = []
            if not audio:
                missing.append('audio')
            if not text:
                missing.append('text')
            return (
                jsonify(
                    {
                        'detail': '生成融合执行单前，需要先完成音乐分析和文本分析',
                        'missing': missing,
                        'hint': '请先完成第2步“音乐分析”和第3步“文本分析”，再执行第4步',
                    }
                ),
                400,
            )

        result = build_fusion_plan(json.loads(audio.markers_json), json.loads(text.scenes_json), report_mode=report_mode)
        row = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()

        if row is None:
            row = FusionPlan(
                project_id=project_id,
                cue_sheet_json=json.dumps(result['cues'], ensure_ascii=False),
                report_markdown=result['report_markdown'],
            )
            db.add(row)
        else:
            row.cue_sheet_json = json.dumps(result['cues'], ensure_ascii=False)
            row.report_markdown = result['report_markdown']

        db.commit()
        log_reason_event(
            db=db,
            event_type='fusion_build',
            term='fusion',
            backend='n/a',
            project_id=project_id,
            req={'report_mode': report_mode},
            resp={'success': True, 'cue_count': len(result.get('cues', [])), 'analysis_mode': result.get('analysis_mode')},
        )
        return jsonify(result)


@app.get(f'{settings.api_prefix}/analysis/<int:project_id>/report')
def get_report(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()

        return jsonify(
            {
                'project': {'id': project.id, 'title': project.title, 'created_at': project.created_at.isoformat()},
                'audio': None
                if not audio
                else {
                    'duration_sec': audio.duration_sec,
                    'bpm': audio.bpm,
                    'tags': json.loads(audio.tags_json),
                    'markers': json.loads(audio.markers_json),
                    'report_markdown': audio.report_markdown,
                },
                'text': None
                if not text
                else {'scenes': json.loads(text.scenes_json), 'report_markdown': text.report_markdown},
                'fusion': None
                if not fusion
                else {'cues': json.loads(fusion.cue_sheet_json), 'report_markdown': fusion.report_markdown},
            }
        )


@app.get(f'{settings.api_prefix}/analysis/<int:project_id>/export')
def export_report_assets(project_id: int):
    export_type = (request.args.get('type') or '').strip().lower()
    if export_type not in {'cue_csv', 'sfx_zip'}:
        return jsonify({'detail': "type must be one of: cue_csv, sfx_zip"}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()
        if not fusion:
            return jsonify({'detail': 'Fusion plan not found, generate fusion first'}), 400

        cues = json.loads(fusion.cue_sheet_json)

        if export_type == 'cue_csv':
            out = export_cue_csv(project_id=project.id, project_title=project.title, cues=cues)
            log_reason_event(
                db=db,
                event_type='export_download',
                term='cue_csv',
                backend='n/a',
                project_id=project_id,
                req={'type': 'cue_csv'},
                resp={'success': True, 'cue_count': len(cues)},
            )
            return send_file(out, as_attachment=True, download_name=out.name, mimetype='text/csv')

        out_zip, summary = export_sfx_zip(project_id=project.id, project_title=project.title, cues=cues)
        log_reason_event(
            db=db,
            event_type='export_download',
            term='sfx_zip',
            backend='n/a',
            project_id=project_id,
            req={'type': 'sfx_zip'},
            resp={
                'success': True,
                'required_count': summary.get('required_count'),
                'found_count': summary.get('found_count'),
                'missing_count': summary.get('missing_count'),
                'missing_sfx': summary.get('missing_sfx', []),
            },
        )
        # response headers provide a quick machine-readable summary
        resp = send_file(out_zip, as_attachment=True, download_name=out_zip.name, mimetype='application/zip')
        resp.headers['X-SFX-Required-Count'] = str(summary['required_count'])
        resp.headers['X-SFX-Found-Count'] = str(summary['found_count'])
        resp.headers['X-SFX-Missing-Count'] = str(summary['missing_count'])
        return resp


@app.get(f'{settings.api_prefix}/ops/funnel')
def ops_funnel():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as db:
        rows = db.execute(
            select(ReasoningLog.event_type, ReasoningLog.project_id, ReasoningLog.output_json)
            .where(ReasoningLog.created_at >= cutoff)
            .where(ReasoningLog.project_id.is_not(None))
        ).all()

    step_projects: dict[str, set[int]] = {
        'graph_reason': set(),
        'search_sfx': set(),
        'fusion_build': set(),
        'export_download': set(),
    }
    export_type_count = {'cue_csv': 0, 'sfx_zip': 0}

    for event_type, project_id, output_json in rows:
        if event_type in step_projects:
            step_projects[event_type].add(int(project_id))
        if event_type == 'export_download':
            try:
                payload = json.loads(output_json)
            except json.JSONDecodeError:
                payload = {}
            # payload doesn't include type; infer with counts
            if payload.get('required_count') is not None:
                export_type_count['sfx_zip'] += 1
            else:
                export_type_count['cue_csv'] += 1

    base = len(step_projects['graph_reason']) or 1
    funnel = []
    for step in ('graph_reason', 'search_sfx', 'fusion_build', 'export_download'):
        cnt = len(step_projects[step])
        funnel.append({'step': step, 'project_count': cnt, 'rate_from_graph_reason': round(cnt / base, 4)})

    return jsonify({'days': days, 'funnel': funnel, 'export_type_count': export_type_count})


@app.get(f'{settings.api_prefix}/ops/recommendations')
def ops_recommendations():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    low_match_terms: dict[str, int] = {}
    missing_sfx_terms: dict[str, int] = {}

    with SessionLocal() as db:
        rows = db.execute(
            select(ReasoningLog.event_type, ReasoningLog.term, ReasoningLog.output_json)
            .where(ReasoningLog.created_at >= cutoff)
        ).all()

    for event_type, term, output_json in rows:
        try:
            payload = json.loads(output_json)
        except json.JSONDecodeError:
            payload = {}

        if event_type == 'search_sfx':
            matches = payload.get('matches') or []
            if len(matches) == 0:
                low_match_terms[term] = low_match_terms.get(term, 0) + 1
        elif event_type == 'export_download':
            for m in payload.get('missing_sfx', []):
                if not m:
                    continue
                missing_sfx_terms[m] = missing_sfx_terms.get(m, 0) + 1

    top_low_match = sorted(low_match_terms.items(), key=lambda x: x[1], reverse=True)[:15]
    top_missing = sorted(missing_sfx_terms.items(), key=lambda x: x[1], reverse=True)[:15]

    return jsonify(
        {
            'days': days,
            'recommendations': {
                'expand_lexicon_for_terms': [{'term': k, 'count': v} for k, v in top_low_match],
                'add_sfx_assets_for_terms': [{'term': k, 'count': v} for k, v in top_missing],
            },
        }
    )


@app.get(f'{settings.api_prefix}/ops/lexicon-draft')
def ops_lexicon_draft():
    try:
        days = int((request.args.get('days') or '7').strip())
    except ValueError:
        days = 7
    with SessionLocal() as db:
        draft = generate_lexicon_draft(db, days=days)
    return jsonify(draft)


@app.get(f'{settings.api_prefix}/ops/lexicon-review')
def ops_lexicon_review_list():
    with SessionLocal() as db:
        rows = db.execute(
            select(DraftReview.candidate, DraftReview.target_head, DraftReview.status, DraftReview.note, DraftReview.updated_at)
        ).all()
    out = [
        {
            'candidate': c,
            'target_head': h,
            'status': s,
            'note': n,
            'updated_at': (u.isoformat() if u else None),
        }
        for c, h, s, n, u in rows
    ]
    return jsonify({'count': len(out), 'items': out})


@app.post(f'{settings.api_prefix}/ops/lexicon-review')
def ops_lexicon_review_upsert():
    payload = request.get_json(force=True)
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'detail': 'items must be a list'}), 400

    valid_status = {'pending', 'approved', 'rejected'}
    upserted = 0
    with SessionLocal() as db:
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get('candidate') or '').strip()
            if not candidate:
                continue
            status = str(item.get('status') or 'pending').strip().lower()
            if status not in valid_status:
                continue
            target_head = str(item.get('target_head') or '').strip()
            note = str(item.get('note') or '').strip()

            exist = db.execute(select(DraftReview).where(DraftReview.candidate == candidate)).scalar_one_or_none()
            if exist is None:
                exist = DraftReview(candidate=candidate, target_head=target_head, status=status, note=note)
                db.add(exist)
            else:
                if target_head:
                    exist.target_head = target_head
                exist.status = status
                exist.note = note
            upserted += 1
        db.commit()

    return jsonify({'ok': True, 'upserted': upserted})


@app.post(f'{settings.api_prefix}/ops/lexicon-draft/apply')
def ops_lexicon_draft_apply():
    payload = request.get_json(force=True)
    draft_lexicon = payload.get('draft_lexicon')
    draft_items = payload.get('draft_items')
    min_confidence = payload.get('min_confidence')
    only_selected = bool(payload.get('only_selected', False))
    mode = (payload.get('mode') or 'merge').strip().lower()
    dry_run = bool(payload.get('dry_run', False))

    # Alternative input: draft_items (with candidate/target_head/confidence/selected)
    applied_review_items = []
    if draft_lexicon is None and isinstance(draft_items, list):
        threshold = 0.0
        if min_confidence is not None:
            try:
                threshold = max(0.0, min(1.0, float(min_confidence)))
            except (TypeError, ValueError):
                return jsonify({'detail': 'min_confidence must be a number in [0,1]'}), 400
        mapped: dict[str, list[str]] = {}
        for item in draft_items:
            if not isinstance(item, dict):
                continue
            selected = item.get('selected', True)
            if only_selected and not selected:
                continue
            try:
                conf = float(item.get('confidence', 0.0) or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf < threshold:
                continue
            head = str(item.get('target_head') or '').strip()
            cand = str(item.get('candidate') or '').strip()
            if not head or not cand:
                continue
            mapped.setdefault(head, []).append(cand)
            applied_review_items.append({'candidate': cand, 'target_head': head, 'status': 'approved', 'note': 'applied'})
        draft_lexicon = mapped

    if not isinstance(draft_lexicon, dict):
        return jsonify({'detail': 'draft_lexicon must be an object (or provide draft_items)'}), 400
    if mode not in {'merge', 'replace'}:
        return jsonify({'detail': 'mode must be merge or replace'}), 400

    before = get_lexicon()
    if dry_run:
        # preview only
        if mode == 'replace':
            after = {}
            for k, vals in draft_lexicon.items():
                head = str(k).strip()
                if not head:
                    continue
                seen = set()
                uniq = []
                for x in vals:
                    xx = str(x).strip()
                    if xx and xx not in seen:
                        seen.add(xx)
                        uniq.append(xx)
                after[head] = uniq
        else:
            after = dict(before)
            for k, vals in draft_lexicon.items():
                head = str(k).strip()
                if not head:
                    continue
                base = after.get(head, [])
                base += [str(x).strip() for x in vals if str(x).strip()]
                # dedup
                seen = set()
                uniq = []
                for x in base:
                    if x not in seen:
                        seen.add(x)
                        uniq.append(x)
                after[head] = uniq
        return jsonify(
            {
                'ok': True,
                'dry_run': True,
                'before_size': len(before),
                'after_size': len(after),
                'applied_head_count': len(draft_lexicon),
                'applied_item_count': sum(len(v) for v in draft_lexicon.values()),
                'after_lexicon': after,
            }
        )

    if mode == 'merge':
        after = merge_lexicon(draft_lexicon)
    else:
        save_lexicon(draft_lexicon)
        after = get_lexicon()

    if applied_review_items:
        with SessionLocal() as db:
            for item in applied_review_items:
                exist = db.execute(select(DraftReview).where(DraftReview.candidate == item['candidate'])).scalar_one_or_none()
                if exist is None:
                    db.add(
                        DraftReview(
                            candidate=item['candidate'],
                            target_head=item['target_head'],
                            status='approved',
                            note=item['note'],
                        )
                    )
                else:
                    exist.target_head = item['target_head']
                    exist.status = 'approved'
                    exist.note = item['note']
            db.commit()

    return jsonify(
        {
            'ok': True,
            'dry_run': False,
            'mode': mode,
            'before_size': len(before),
            'after_size': len(after),
            'applied_head_count': len(draft_lexicon),
            'applied_item_count': sum(len(v) for v in draft_lexicon.values()),
            'lexicon': after,
        }
    )


if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    app.run(host='0.0.0.0', port=8090, debug=False, use_reloader=False)

import csv
import json
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates


def _safe_slug(text: str) -> str:
    out = ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in text.strip())
    return out[:60] or 'project'


def export_cue_csv(project_id: int, project_title: str, cues: list[dict]) -> Path:
    export_dir = Path('./exports').resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{project_id}_{_safe_slug(project_title)}_cue_{ts}.csv"
    target = export_dir / filename

    headers = [
        'scene_no',
        'target_time_sec',
        'marker_label',
        'dialogue_music_ratio',
        'mix_tip',
        'recommended_sfx',
    ]

    with target.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for cue in cues:
            writer.writerow(
                {
                    'scene_no': cue.get('scene_no'),
                    'target_time_sec': cue.get('target_time_sec'),
                    'marker_label': cue.get('marker_label'),
                    'dialogue_music_ratio': cue.get('dialogue_music_ratio'),
                    'mix_tip': cue.get('mix_tip'),
                    'recommended_sfx': ' | '.join(cue.get('recommended_sfx', [])),
                }
            )

    return target


def export_sfx_zip(project_id: int, project_title: str, cues: list[dict]) -> tuple[Path, dict]:
    export_dir = Path('./exports').resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    sfx_dir = Path('./assets/sfx').resolve()
    sfx_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_name = f"{project_id}_{_safe_slug(project_title)}_sfx_{ts}.zip"
    zip_path = export_dir / zip_name

    required = set()
    for cue in cues:
        for name in cue.get('recommended_sfx', []):
            if name:
                required.add(name.strip())

    found_files = []
    missing = []
    matched_detail = {}
    library = load_sfx_library()
    selected_files = set()

    with zipfile.ZipFile(zip_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            'project_id': project_id,
            'project_title': project_title,
            'required_sfx': sorted(required),
            'found_files': [],
            'missing_sfx': [],
            'generated_at': datetime.now().isoformat(),
        }

        for name in sorted(required):
            cands = match_sfx_candidates(name, library, top_n=3)
            chosen = None
            for c in cands:
                if c['file_name'] not in selected_files:
                    chosen = c
                    break
            if chosen is None and cands:
                chosen = cands[0]

            if chosen is None:
                missing.append(name)
                matched_detail[name] = {'chosen': None, 'candidates': []}
                continue

            selected_files.add(chosen['file_name'])
            matched_path = Path(chosen['file_path'])
            arc_name = f"sfx/{matched_path.name}"
            zf.write(matched_path, arc_name)
            found_files.append(matched_path.name)
            matched_detail[name] = {'chosen': chosen, 'candidates': cands}

        manifest['found_files'] = found_files
        manifest['missing_sfx'] = missing
        manifest['matched_detail'] = matched_detail

        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(
            'README.txt',
            'This bundle is generated from fusion cues. Matching uses Chinese token/semantic expansion and vector-like similarity.\n',
        )

    return zip_path, {
        'required_count': len(required),
        'found_count': len(found_files),
        'missing_count': len(missing),
        'missing_sfx': missing,
    }

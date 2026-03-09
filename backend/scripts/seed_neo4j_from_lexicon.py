#!/usr/bin/env python3
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')


def main():
    import os

    uri = os.getenv('NEO4J_URI', '')
    user = os.getenv('NEO4J_USER', '')
    password = os.getenv('NEO4J_PASSWORD', '')
    database = os.getenv('NEO4J_DATABASE', '') or None

    if not (uri and user and password):
        raise SystemExit('Missing NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD in backend/.env')

    try:
        from neo4j import GraphDatabase
    except Exception:
        raise SystemExit('neo4j package not installed. Run: pip install neo4j')

    lex_path = Path(__file__).resolve().parent.parent / 'assets' / 'sfx' / 'semantic_lexicon.json'
    if not lex_path.exists():
        raise SystemExit(f'Lexicon not found: {lex_path}')

    lex = json.loads(lex_path.read_text(encoding='utf-8'))

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session(database=database) as session:
        session.run('CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE')

        created = 0
        rels = 0
        for head, syns in lex.items():
            head = str(head).strip().lower()
            if not head:
                continue
            session.run('MERGE (:Concept {name:$name})', name=head)
            created += 1

            for s in syns:
                s = str(s).strip().lower()
                if not s:
                    continue
                session.run('MERGE (:Concept {name:$name})', name=s)
                session.run(
                    'MATCH (a:Concept {name:$a}), (b:Concept {name:$b}) '
                    'MERGE (a)-[:SYNONYM]->(b) '
                    'MERGE (b)-[:SYNONYM]->(a)',
                    a=head,
                    b=s,
                )
                rels += 2

        print(f'SEED_OK nodes_upserted~{created} synonym_edges_upserted~{rels}')

    driver.close()


if __name__ == '__main__':
    main()

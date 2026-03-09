#!/usr/bin/env python3
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

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session(database=database) as session:
        n = session.run('MATCH (c:Concept) RETURN count(c) AS n').single()['n']
        r = session.run('MATCH ()-[x:SYNONYM]->() RETURN count(x) AS n').single()['n']
        sample = [x['name'] for x in session.run('MATCH (c:Concept) RETURN c.name AS name LIMIT 10')]

    driver.close()
    print({'concept_nodes': n, 'synonym_edges': r, 'sample': sample})


if __name__ == '__main__':
    main()

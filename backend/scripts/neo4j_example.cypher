// Example concept graph bootstrap
MERGE (a:Concept {name:'脚步声'})
MERGE (b:Concept {name:'足音'})
MERGE (c:Concept {name:'跑步声'})
MERGE (d:Concept {name:'冲锋'})
MERGE (e:Concept {name:'群体脚步'})

MERGE (a)-[:SYNONYM]->(b)
MERGE (b)-[:SYNONYM]->(a)
MERGE (a)-[:RELATED_TO]->(c)
MERGE (a)-[:RELATED_TO]->(d)
MERGE (a)-[:IS_A]->(e)

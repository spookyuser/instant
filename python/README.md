# Instant Python Client

A minimal Python client for Instant's admin API.

## Usage

```python
from instant import init, tx, q, id, schema_from_ts, models

app_id = "my-app"
admin_token = "my-token"

client = init(app_id, admin_token)

schema = schema_from_ts("instant.schema.ts")
Project = models(schema)["projects"]

goal_id = id()
client.transact(tx.projects[goal_id].update(Project(title="Get fit")))

res = client.query(
    q.projects.where({"id": goal_id}, Project),
    {"projects": Project},
)

print(res["projects"][0].title)
```

### Typed queries

`q` builds query dictionaries from models, and `query` casts results to those models.

```python
res = client.query(q.projects.all(Project), {"projects": Project})
```

### Types from schema

`schema_from_ts` loads an Instant TypeScript schema and `models` turns it into Pydantic models for hints.

```python
from instant import schema_from_ts, models

schema = schema_from_ts("instant.schema.ts")
Project = models(schema)["projects"]

Project(title="Test")
```

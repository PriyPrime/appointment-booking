# Documentation

Reference material for the project report. Each file covers one section:

| File | Contents |
|---|---|
| [`modules.md`](modules.md) | Every module, its responsibility and its key functions. |
| [`database.md`](database.md) | Entity–relationship description, keys, constraints and indexes. |
| [`dfd.md`](dfd.md) | Context diagram and level-1 data flow diagram. |
| [`api.md`](api.md) | Complete HTTP API table with auth, parameters and examples. |
| [`workflows.md`](workflows.md) | Step-by-step behaviour of booking, cancelling and reporting. |

## System in one paragraph

"Appointly" is a small appointment booking system for a clinic or salon. A
customer registers, browses services, chooses a provider and books a free
time slot. Providers keep their weekly hours and days off up to date and mark
visits as completed or missed. An administrator maintains the service
catalogue and reads revenue and no-show reports. The application is a Flask
JSON API that also serves a plain HTML/CSS/JavaScript frontend, backed by a
single SQLite file.

## Architecture at a glance

```
Browser (plain HTML/CSS/ES modules)
      |
      |  fetch() -> JSON over HTTP, session cookie
      v
Flask app (app.py)
  routes/            request handling and validation
  services_logic/    slot generation, notification worker
      |
      |  sqlite3 (stdlib, foreign keys on)
      v
SQLite file (schema.sql)
```

Three layers, one direction of dependency:

1. **Presentation** — `static/`. Knows only URLs and JSON shapes.
2. **Application** — `routes/` and `services_logic/`. Enforces every business
   rule; never renders HTML.
3. **Data** — `db.py` and `schema.sql`. No business decisions beyond
   constraints and indexes.

The frontend never opens the database, and the database never validates
business rules that belong to the application layer (except where a
constraint is the last line of defence, such as the unique booked-slot
index).

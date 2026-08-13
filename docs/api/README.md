# API schema

`openapi.json` is exported directly from the running FastAPI app
(`app.openapi()`), so it's always in sync with the real request/response
models — not hand-maintained.

Regenerate after changing any endpoint or Pydantic schema:

```bash
docker exec saas_ai_core python -c "
import json
from app.main import create_application
schema = create_application().openapi()
with open('/tmp/openapi.json', 'w') as f:
    json.dump(schema, f, indent=2, ensure_ascii=False)
"
docker cp saas_ai_core:/tmp/openapi.json docs/api/openapi.json
```

For interactive exploration while the stack is running, use Swagger UI
directly instead: http://localhost:8000/docs (or `/redoc`).

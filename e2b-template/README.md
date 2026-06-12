# E2B Sandbox Setup

Agent Board uses [E2B](https://e2b.dev) cloud sandboxes to execute paper reproduction tasks. Follow these steps to get it running.

## Prerequisites

- An E2B account ([e2b.dev](https://e2b.dev))
- An Anthropic API key (Agent Board uses Claude as the Codex worker)
- Node.js >= 18 (for E2B CLI)

## 1. Install E2B CLI

```bash
npm install -g @e2b/cli
e2b auth login
```

## 2. Build & publish the sandbox template

```bash
cd e2b-template
e2b template build
```

This reads `e2b.toml` (template metadata & resource limits) and `e2b.Dockerfile` (image definition with pre-installed ML/scientific stack), then uploads the built image to E2B cloud.

On success you'll see the template ID (default: `paperbot-repro`). Verify it matches `E2B_TEMPLATE` in your `.env`.

### What's in the template

| Layer | Packages |
|-------|----------|
| System | build-essential, git, curl, wget, ffmpeg, libgl1 |
| ML/Science | torch (CPU), transformers, datasets, numpy, scipy, pandas, matplotlib, scikit-learn, opencv |
| Dev tools | pytest, black, pylint, flake8, jupyter |
| Node | typescript, ts-node |

## 3. Configure environment variables

```bash
cp env.example .env
```

Required variables for Agent Board sandbox:

```bash
E2B_API_KEY=e2b_...                        # from E2B dashboard
E2B_TEMPLATE=paperbot-repro               # must match template_id in e2b.toml
ANTHROPIC_API_KEY=sk-ant-...              # Claude powers the Codex worker
```

Optional:

```bash
E2B_SANDBOX_TIMEOUT_SECONDS=3600          # sandbox TTL (default: 1 hour)
CODEX_MAX_ITERATIONS=100                  # max tool-loop iterations per task
```

## 4. Start backend & frontend

```bash
# Backend
python -m uvicorn paperbot.api.main:app --reload --port 8000

# Frontend
cd web && npm run dev
```

## 5. Use Agent Board

1. Open `http://localhost:3001/studio`
2. Select a paper
3. Enter the Agent Board
4. Click **Run All** to start the pipeline

The pipeline will: create a sandbox -> plan tasks -> execute code in the sandbox -> run E2E verification -> display results.

## File reference

| File | Purpose |
|------|---------|
| `e2b.toml` | Template metadata: ID, name, resource limits (CPU, memory) |
| `e2b.Dockerfile` | Image build script: base image, system deps, Python/Node packages |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `E2B sandbox creation timeout` | Template not built or API key missing | Run `e2b template build` and check `E2B_API_KEY` |
| `paperbot-repro template not found` | Template ID mismatch | Ensure `E2B_TEMPLATE` in `.env` matches `template_id` in `e2b.toml` |
| Sandbox dies mid-pipeline | Exceeded `E2B_SANDBOX_TIMEOUT_SECONDS` | Increase the value or use the **Continue** button to resume |

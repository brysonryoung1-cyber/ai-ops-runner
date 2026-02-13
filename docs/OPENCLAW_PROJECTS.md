# OpenClaw V1 — Projects

## Project Registry

Projects are registered in `configs/openclaw/projects.yaml`.  Each project
defines which lanes are active, which repos to target, and any
platform-specific configuration.

## Current Projects

### algo-nt8-orb (Active)

- **Lanes**: ORB Analysis (read-only)
- **Repo**: `git@github.com:brysonryoung1-cyber/algo-nt8-orb.git`
- **Jobs**: `orb_doctor`, `orb_review_bundle`, `orb_score_run`
- **Status**: Active — running via existing `ai-ops-orb-daily` timer

### SoraWorld (Placeholder — V2)

- **Lanes**: Content (generation + QC), Browser (analytics, disabled)
- **Platform**: YouTube / social
- **Status**: Placeholder — no publishing logic yet
- **Notes**: Content generation and QC queue will be implemented in V2.
  Browser lane for analytics scraping requires explicit enable in policies.

### AI-ASMR (Placeholder — V2)

- **Lanes**: Content (generation + QC), Browser (analytics, disabled)
- **Platform**: YouTube / social
- **Status**: Placeholder — no publishing logic yet
- **Notes**: Similar pipeline to SoraWorld with different content templates
  and brand guidelines.

## Adding a New Project

1. Add an entry to `configs/openclaw/projects.yaml` with:
   - `name`: Unique project identifier
   - `lanes`: List of active lanes (`infra`, `orb`, `content`, `browser`)
   - `repo` (if applicable): Git remote URL (must be in `configs/repo_allowlist.yaml`)
   - `platform` (if applicable): Target platform for content/browser lanes

2. If the project uses ORB lane, add the repo to `configs/repo_allowlist.yaml`.

3. If the project uses Content or Browser lanes, ensure:
   - The lane is enabled in `configs/openclaw/policies.yaml`
   - Platform credentials are stored in the tenant secrets directory
   - Rate limits are configured in the policies file

## Multi-Tenant Project Isolation

In the multi-tenant model (V4+), each tenant has:
- Their own `projects.yaml` (scoped to their allowed repos/platforms)
- Isolated secrets directory
- Separate artifact namespaces
- Independent rate limits

A project can only reference repos and platforms that the tenant is authorized
to use.  The global policies file sets the ceiling; tenant-level policies can
only be more restrictive.

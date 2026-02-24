# Soma Locked Spec — Zane Kajabi (Canonical)

**Status**: LOCKED — All agents/actions must follow this spec. No drift.

**Scope**: OpenClaw / ai-ops-runner (Soma/Kajabi). No NT8/MNQ.

---

## 1. Brand

- Site: zane-mccourtney.mykajabi.com (Soma)
- Kajabi admin: app.kajabi.com (with site bootstrap when 404)
- Brand identity, landing copy, CTAs: defined in Kajabi UI

---

## 2. Tiers

- **Home User Library**: Free / entry tier
- **Practitioner Library**: Paid tier (superset of Home above-paywall content)

---

## 3. Offers

- **Required checkout URLs** (fail-closed if not found on memberships page):
  - `/offers/q6ntyjef/checkout`
  - `/offers/MHMmHyVZ/checkout`
- Offers must be discoverable via Kajabi admin memberships/offers pages

---

## 4. Pages

- Landing page, nav, site structure: configured in Kajabi
- Pages exist and are reachable (manual verification or discover)

---

## 5. Libraries

- **Home User Library**: Product with modules and lessons
- **Practitioner Library**: Product with modules and lessons
- Both products must be discoverable via soma_kajabi_discover

---

## 6. Video Mapping

- Gmail harvest: `from:(Zane McCourtney) has:attachment`
- Video manifest: one row per Zane email video
  - Columns: subject, timestamp, filename, mapped lesson, status (attached | raw_needs_review)
- Status values: `attached`, `raw_needs_review`, `unmapped`, `mapped`

---

## 7. Mirror (Home → Practitioner)

- **Invariant**: All above-paywall Home lessons MUST exist in Practitioner
- Same module, title, description, video, published/draft state
- Exceptions list MUST be empty for PASS

---

## 8. Legal

- Terms, privacy, disclaimers: configured in Kajabi
- No secrets in artifacts

---

## 9. Constraints

- No secrets in acceptance artifacts
- Fail-closed on: offer URL mismatch, mirror exceptions non-empty, required artifacts missing
- RAW module: must be present (for raw_needs_review videos)

---

## 10. Required Artifacts (per run)

1. **Final Library Snapshot**
   - Home + Practitioner full trees
   - module, lesson, published/draft, above/below paywall, video filename

2. **Video Manifest**
   - One row per Zane email video: subject, timestamp, filename, mapped lesson, status (attached | raw_needs_review)

3. **Mirror Report (Home → Practitioner)**
   - Verify all above-paywall Home lessons exist in Practitioner
   - Exceptions list MUST be empty for PASS

4. **Changelog**
   - Lessons created/updated, videos attached, moves to RAW, open questions

---

## 11. Open Decisions

- Items listed but not required for PASS
- Tracked in changelog / punchlist

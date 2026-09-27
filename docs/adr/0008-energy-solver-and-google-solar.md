# 0008: Energy dispatch on OR-Tools; Google Solar kept off the open map

## Status
Accepted (Phase 8)

## Context
Section 9.9 of the brief asks for two things:

- a dispatch LP in **Pyomo + HiGHS**;
- **Google Solar API** as the primary solar source, with NASA POWER or
  Global Solar Atlas as the fallback.

Two facts shape the implementation:

- **The LP is small.** It has about 1,500 continuous variables (12
  representative days × 24 hours × 5 series, plus 4 sizes) and is solved
  per site on request.
- **Solar API content is restricted.** It is `RESTRICTED_GOOGLE` under the
  Google Maps Platform terms (see docs/verification.md item 1): short-term
  caching only, and no use with a non-Google map. The run page is built on
  the open MapLibre canvas (ADR 0002).

## Decision
1. **Solve the LP with OR-Tools' GLOP**, already a dependency since Phase 6.
   We don't add Pyomo and highspy.
   - The model is written directly against the linear-solver API.
   - It solves in about 0.1 s per site.
   - The formulation is plain LP, so a Pyomo port stays mechanical if one is
     wanted.
2. **Google Solar is additive, never required.**
   - When a key, a monthly cost cap and the INR/USD rate are all configured,
     `buildingInsights:findClosest` supplies the host roof's maximum array
     and yield.
   - Otherwise, or when the API returns NOT_FOUND or a building more than
     150 m away, the site uses NASA POWER and a canopy over its bays.
   - Only Building Insights is called; Data Layers is not.
3. **Google results follow the licence.**
   - Tagged `RESTRICTED_GOOGLE`.
   - Cached for at most 30 days, then deleted from the site's record.
   - Never written to the raw lake.
   - **Not returned by the API used by the open-map pages.** Those pages get
     the NASA/canopy result, plus a flag saying a Google result exists and is
     viewable only in Google map mode.

## Consequences
- The energy tab works without any paid key, with the NASA/canopy path as
  the default. That default holds until Solar API coverage for Pune is
  measured (`make solar-coverage`).
- A Google-backed recommendation needs a Google-canvas view to be shown.
  That view isn't built on the run page yet, so today Google data can only
  inform the result through that future view.
- The 30-day expiry is enforced on read. A site not reopened after expiry
  keeps expired Google content until a scheduled purge exists. Adding that
  purge to the beat schedule is a follow-up.

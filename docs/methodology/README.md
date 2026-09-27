# Methodology write-ups

Each deterministic engine gets a methodology note here once it's implemented
(Section 9 of the project brief).

- [osm-ingestion-phase1.md](osm-ingestion-phase1.md): OSM roads, POIs, boundaries, land cover
- [charger-fusion.md](charger-fusion.md): existing chargers from several sources
- [population-h3.md](population-h3.md): WorldPop population on H3
- [demand.md](demand.md): EV forecast, public charging demand, gap (Phase 3)
- [candidates-feasibility.md](candidates-feasibility.md): candidate sites and feasibility rules (Phase 4)
- [scoring.md](scoring.md): catchments, sub-scores, weight profiles, rank robustness (Phase 5)
- [optimisation.md](optimisation.md): MCLP plan, why-not, what-if, strategies and catchments (Phase 6)
- [energy.md](energy.md): solar resource, PV + battery dispatch LP, recommendation (Phase 8)
- [sizing-finance.md](sizing-finance.md): Erlang-C + SimPy charger sizing, CAPEX/OPEX, NPV/IRR with Monte Carlo (Phase 7)
- [agents.md](agents.md): agent graph, tools, numeric-claim validator, policy retrieval, regions and corridors (Phase 9)
- [grid-and-calibration.md](grid-and-calibration.md): Grid Impact (module E), DISCOM headroom, OCPI feeds, LightGBM calibration (Phase 10)

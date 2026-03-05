# MAGMA Muistiarkkitehtuuri v1.1

## Yleiskatsaus
Multi-Agent Generative Memory Architecture (MAGMA) — append-only muistikerros WaggleDance-järjestelmälle.

## Kerrokset (Layers)

### Layer 1: Foundation (✅ Toteutettu)
- **AuditLog** — append-only SQLite auditointiloki (`data/audit_log.db`)
- **ChromaDBAdapter** — ohut sovitin olemassa olevan MemoryStoren päälle
- **MemoryWriteProxy** — roolipohjainen kirjoitussuoja (admin/worker/enricher/readonly)
- **AgentRollback** — agentin kirjoitusten peruminen istunnon tai spawn-puun mukaan

### Kirjoitustilat
| Tila | Kuvaus |
|------|--------|
| `new` | Uusi dokumentti working-kerrokseen |
| `correction` | Korjaus olemassa olevaan dokumenttiin |
| `invalidate_range` | Dokumentin mitätöinti (ei poisto) |

### Roolit
| Rooli | new | correction | invalidate |
|-------|-----|-----------|-----------|
| admin | ✅ | ✅ | ✅ |
| enricher | ✅ | ✅ | ❌ |
| worker | ✅ | ❌ | ❌ |
| readonly | ❌ | ❌ | ❌ |

### Tietoturvaperiaatteet
1. **Ei ylikirjoitusta** — overwrite-tilaa ei ole
2. **Alkuperäisdataa ei kosketa** — original-kerros on pyhä
3. **Auditointipolku** — jokainen kirjoitus kirjataan
4. **Spawn-puun seuranta** — lapsiagentit perutaan rekursiivisesti

## Tulevat kerrokset
- Layer 2: Selective Replay
- Layer 3: Overlay Networks
- Layer 4: Cross-agent Memory Sharing

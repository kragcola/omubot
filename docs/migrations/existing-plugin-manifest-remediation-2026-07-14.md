# ManifestV3 平台合同 D3 迁移清单（2026-07-14）

## Parser / Model

- [x] ad hoc Bus manifest overlay → shared typed ManifestV3 parser。
- [x] Index object-only parse → shared typed parse + diagnostic status。
- [x] legacy `kernel.manifest.PluginManifest` generator → canonical V3 model/compat alias。
- [x] convention-only v3 → checked-in JSON Schema + executable repository gate。
- [x] display-only min version → runtime registration/discovery gate。

## Identity / Dependencies

- [x] directory/class/manifest split identity → one validated name + duplicate rejection。
- [x] dynamic dependency attributes → typed required/optional maps。
- [x] legacy `dependencies` ambiguity → explicit required alias semantics。
- [x] priority/shared-ctx optional ordering → explicit manifest optional graph。
- [x] Admin dependency summary → required/optional/degraded truth。

## Config / Lifecycle / Capability

- [x] fixed config filenames + split defaults → manifest-resolved paths and one apply-mode/restart-field truth。
- [x] Food/Memo raw create_task sets → owned tasks with exception retrieval + cancel/await shutdown。
- [x] capability-only constant healthy → runtime service availability/probe health。
- [x] runtime lifecycle convention → executable CI gate。

## Surface / Compatibility

- [x] context fallback metadata → explicit canonical manifest fields。
- [x] stale sticker/version/toggle tables → current manifest-derived docs。
- [x] old plugin examples/未落地能力声明 → canonical v3 docs only。
- [x] repository formal packages fail-open → strict fail-closed；legacy external/single-file blocked compatibility retained。
- [x] frontend plugin detail exposes required/optional dependency and real capability health。
- [x] rollback/build/runtime/zero-outbound evidence recorded。

## Completion Audit Hardening

- [x] class/manifest repeated `description` parity；显式空 dependency map 不再绕过 checker。
- [x] optional dependency missing/disabled/version/startup failure → enabled-but-degraded，恢复后清除；Bus/Index/Admin/system health 同源。
- [x] Vision 正向 healthy、failure→success recovery 与空白 200 fail-closed。
- [x] lifecycle workflow 纳入 Schedule、Memory、LearningCoordinator 与 composition ownership contracts。
- [x] typed boundary gate 扩至 44 targets，覆盖 canonical Tool ABI、debug owner 与 style extraction service。

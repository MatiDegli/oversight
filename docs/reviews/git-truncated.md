Commits: `1c1cb41` (protección y regresiones); `HEAD` (este commit: entrega y prueba de evidencia).
BLOCKING REVIEW.diff:1: corregido con esta entrega en español de ocho líneas y su prueba, que falla si falta el documento o la evidencia exigida.
Callers revisados: `Tree.head/commit_all`, `Workspace.reset/create_worktree/_remove_worktree`, `ChangeContext.git` en proposal, approval, merge, revision, recovery, files y delivery; `lead_probe.demo_graph`; `yes_no/merge-base` sólo usan el código de salida; merge directo sólo añade diagnóstico.
Pruebas: 20 regresiones de truncamiento aprobadas; 1 prueba de entrega comprobada en rojo por documento ausente y luego en verde; ninguna aserción existente debilitada.
Gates sin pipes sobre el tip de este commit: `make check`: exit 0; 2338 pytest + 17 de límites frontend, 646 Vitest y 40 Node; lint, tipos, 11 límites de imports, contratos y secretos aprobados.
`make demo-check`: exit 0; 3/3 ejecuciones desde inicio limpio con modelo falso, sin créditos ni llamadas a Meta/Baseten.
Nota >500 líneas: backend/src/oversight/workspace/core.py: 503; el guard agrega dos líneas netas al módulo existente de 501; dividirlo queda fuera de alcance de esta corrección puntual.
CONTESTED: none

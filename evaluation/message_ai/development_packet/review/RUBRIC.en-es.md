# Proposed bilingual development-label rubric / Rúbrica bilingüe propuesta

Status: synthetic engineering draft, no independent review has occurred. All examples
are development material permanently. This packet is not a qualified holdout and
its labels do not establish model accuracy. No provider execution is authorized.

Estado: borrador sintético de ingeniería, sin revisión independiente. Todos los
ejemplos son material de desarrollo de forma permanente. No constituyen un conjunto
reservado de evaluación ni demuestran precisión. No se autorizan llamadas al proveedor.

## Independent labeling / Etiquetado independiente

Two bilingual reviewers complete separate copies of blind-worksheet.csv before
seeing proposed labels, simulation outputs, each other's answers, or model outputs.
Leave identity/evidence fields blank until actual reviewers participate. A hash or
synthetic reviewer ID must never substitute for real completed review records.

Dos revisores bilingües completan copias separadas de blind-worksheet.csv antes de
ver etiquetas propuestas, simulaciones, respuestas del otro revisor o resultados
del modelo. Los campos de identidad y evidencia quedan vacíos hasta la revisión real.
Un hash o identificador sintético no sustituye un registro de revisión real.

Label only the sanitized selected message plus its explicit speaker/source metadata.
Do not invent missing conversation, sender identity, link contents, payment history
or demographic risk. Decide possible warning signs, not whether a person is criminal.
A no-warning label is not a guarantee of safety or sender verification.

Etiquete solo el texto saneado seleccionado y los metadatos explícitos de autoría y
origen. No invente conversación, identidad, contenido de enlaces ni historial de pagos.
Determine posibles señales de advertencia, no culpabilidad. La ausencia de señales
no garantiza seguridad ni verifica al remitente.

| Assessment / Evaluación | Meaning / Significado |
| --- | --- |
| warning | Supported possible warning sign in clear incoming text; select only supported categories. / Posible señal respaldada por texto entrante claro; seleccione solo categorías respaldadas. |
| no_warning | Clear text without supported warning categories; ordinary gifts, generic urgency, negation and protective advice are not enough alone. / Texto claro sin categorías respaldadas; regalos normales, urgencia genérica, negación y consejos protectores no bastan. |
| abstain | Missing decisive context/attribution, mixed or unsupported source, contradictory or adversarial instructions requiring a stop. / Falta contexto o autoría decisiva, origen mixto o no admitido, contradicción o instrucciones adversarias que exigen detenerse. |

| Category / Categoría | Proposed semantic boundary / Límite semántico propuesto |
| --- | --- |
| AI_CREDENTIAL_REQUEST | Request to disclose account passwords or authentication codes to the correspondent, not independent entry into a known service. / Solicitud de revelar contraseñas de cuenta o códigos de autenticación al interlocutor, no de introducirlos de forma independiente en un servicio conocido. |
| AI_PAYMENT_PRESSURE | A payment or transfer request paired with pressure to act. / Solicitud de pago o transferencia acompañada de presión para actuar. |
| AI_PRETEXT | A claimed identity or situation used to induce a consequential action; an unfamiliar name alone is not evidence of impersonation. / Una identidad o situación alegada para inducir una acción con consecuencias; un nombre desconocido por sí solo no demuestra suplantación. |
| AI_VERIFICATION_BYPASS | Secrecy from trusted contacts or bypassing normal independent verification while requesting consequential action. / Secreto frente a contactos de confianza u omisión de la verificación independiente habitual al solicitar una acción con consecuencias. |
| AI_CONSEQUENTIAL_URGENCY | Urgency coupled to a consequential action, with both supported by the text; generic hurry alone is insufficient. / Urgencia ligada a una acción con consecuencias, ambas respaldadas; la prisa genérica no basta. |

Spelling, HTTP alone, names and demographic attributes must not establish warnings.
La ortografía, HTTP por sí solo, los nombres y los atributos demográficos no determinan advertencias.

Categories can overlap. Record all and only supported categories, separately from the
assessment label. A disagreement on category set is not identical to a disagreement
on warning versus no-warning. Record the distinction during adjudication.

Las categorías pueden coincidir. Registre todas y solo las respaldadas, separadas de
la etiqueta de evaluación. Discrepar sobre categorías no equivale a discrepar sobre
la existencia de advertencias. Distinga ambos casos durante la adjudicación.

## Evidence spans / Fragmentos de evidencia

Use zero-based Unicode code-point offsets, start inclusive/end exclusive, in the
exact NFC sanitized text—not UTF-16 units, UTF-8 bytes or visual grapheme positions.
Select one to three non-overlapping ordered spans per category, containing real
supporting words. A placeholder alone never supports a claim about hidden content.
Check quoted/negated text and speaker ownership. An in-range span is not proof that
it semantically supports the category. Python `text[start:end]` is a convenience for
this indexing convention, not a substitute for human reading.

Use índices desde cero de puntos de código Unicode; inicio incluido y fin excluido,
en el texto saneado NFC exacto. No use unidades UTF-16, bytes UTF-8 ni posiciones de
grafemas visibles. Seleccione de uno a tres fragmentos ordenados sin solapamiento por
categoría que contengan palabras de apoyo reales. Un marcador por sí solo no permite
inferir contenido oculto. Revise citas, negaciones y autoría. Un índice válido no
prueba que el fragmento respalde semánticamente la categoría.

## Pipeline and evidence boundaries / Límites del flujo y la evidencia

Keep the proposed AI text label separate from expected final mobile behavior:
- Withheld-link no-warning text remains final unknown/inconclusive; links are unchecked.
- Withheld-link warning may be partial/suspicious, with no completed-check deduction.
- A simulated independent link match retains high-risk/partial presentation even when
  the AI text label is no-warning. This is a backend contract exercise, not proof of
  live Google results or authorization for mobile joint-link submission.
- Deterministic qualified rules, hostile stops, unknown speaker and unsupported quotes
  may skip AI. Their behavior cannot count as model accuracy or provider latency.
- No packet label or offline output proves billing. Unknown receipts remain unknown;
  actual settlement/recovery must be tested independently.

Mantenga la etiqueta de texto de IA separada del comportamiento final móvil:
- Texto sin advertencias con enlaces omitidos sigue siendo desconocido/no concluyente.
- Una advertencia con enlace omitido puede ser parcial/sospechosa, sin descuento de consulta completada.
- Una coincidencia de amenaza independiente simulada mantiene riesgo alto/parcial aunque
  la IA no identifique señales. No es una comprobación real de Google ni permiso de envío.
- Las reglas, detenciones, autoría desconocida y citas no admitidas pueden omitir la IA;
  no cuentan como precisión del modelo ni latencia del proveedor.
- Ninguna etiqueta o simulación demuestra facturación. Se requieren pruebas de liquidación separadas.

## Adjudication and split independence / Adjudicación e independencia

1. Preserve both original reviewer records; a real adjudicator documents each unresolved
   assessment/category/span/context disagreement and rationale before model execution.
2. Maintain the same family across EN/ES translations, paraphrases, formatting, speaker
   counterfactuals and Unicode variants. Reviewer-led semantic search is required;
   normalized exact-match detection alone does not establish independence.
3. This exposed packet and all descendants stay development-only, even after review.
   Create fresh permitted holdout families outside all prior prompts, examples, tuning,
   engineering tests and model-selection material. Never relabel this packet as holdout.
4. Freeze independently reviewed cohort membership, rubric, labels, spans and family/split
   registry before evaluating the holdout. Record multi-label category denominators and
   correlated translation families; 40 paired cases are not 40 independent samples.
5. Owner reviews actual data rights, reviewer evidence, final acceptance thresholds,
   profile and execution budget before any live stage. No thresholds are approved here.

1. Conserve los registros originales de ambos revisores; una persona real adjudica y
   documenta discrepancias de evaluación, categorías, contexto y fragmentos antes del modelo.
2. Agrupe traducciones, paráfrasis, cambios de formato, autoría y Unicode en una misma
   familia. La detección automática de coincidencias exactas no demuestra independencia.
3. Este paquete ya expuesto y sus derivados siguen siendo de desarrollo tras la revisión.
   Cree familias nuevas autorizadas para la evaluación reservada; nunca recicle este paquete.
4. Congele cohortes, rúbrica, etiquetas, fragmentos y familias antes de evaluar. Declare
   denominadores multietiqueta y traducciones correlacionadas, no muestras independientes.
5. El propietario revisa permisos reales, evidencia de revisión, criterios, perfil y
   presupuesto antes de cualquier ejecución real. Aquí no se aprueban umbrales.

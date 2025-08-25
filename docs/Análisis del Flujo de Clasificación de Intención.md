Análisis del Flujo de Clasificación de Intención
Flujo desde la Consulta al Bot hasta la Respuesta

Cuando el usuario ingresa un mensaje (por ejemplo, “Hola”), el orquestador (mcp-core/orchestrator.py) primero envía el texto al servicio de clasificación de intención (llm_docs-mcp). Este servicio emplea un clasificador híbrido que combina búsqueda de frases (RAG) y un modelo de lenguaje (LLM) como respaldo. El flujo simplificado es:

Clasificador RAG (IntentEngine): Intenta encontrar la intención buscando coincidencias en los documentos JSON de services/llm_docs-mcp/documents. Cada entrada JSON describe una posible intención o respuesta (ej. saludos, despedidas, preguntas frecuentes) con frases de ejemplo (“user_says”) que deberían coincidir con la consulta del usuario
GitHub
. El IntentEngine normaliza la entrada (minúsculas, sin tildes) y calcula puntajes de similitud:

Alias exactos: Si la frase del usuario coincide con algún alias definido para una intención, obtiene un puntaje alto inmediato. (En los JSON de preguntas frecuentes, no se definieron alias explícitos para “hola” u otros saludos; solo están las frases en “user_says”, lo cual resulta problemático más adelante).

Coincidencia de palabras (Bag-of-Words): Si no hay alias, calcula solapamiento de palabras clave entre la consulta y los campos de cada entrada (alias, user_says, título, texto, tags)
GitHub
. Adicionalmente, aplica una pequeña bonificación si detecta ciertas palabras clave indicativas de subintención (por ejemplo, “hola” indica saludo según KW_FAQ)
GitHub
.

Selecciona la entrada con mayor puntaje y determina la intención principal (intent) y sub-intención (sub_intent) según las etiquetas (tags) y metadatos de esa entrada
GitHub
. Por ejemplo, una entrada con tag “faq” y subcategoría “saludo” debería mapear a intent=faq, sub_intent=saludo.

Umbral de confianza: El IntentEngine tiene un umbral fijo UMBRAL_SCORE = 0.18
GitHub
. Si el mejor puntaje de similitud es menor a 0.18, considera que no hay suficiente confianza y devuelve la intención como “n/a” (no definida)
GitHub
. En ese caso, también sub_intent queda None.

Fallback al LLM: Si el RAG no identificó claramente la intención (es decir, intent=n/a), el sistema recurre al modelo de lenguaje para clasificar solo la categoría principal de la consulta
GitHub
. El prompt le pide al LLM que etiquete la consulta exactamente como una de: faq, documento, agenda, reclamo, tramite o n/a
GitHub
. Nota: Aquí vemos una posible limitación: no se menciona “saludo” ni otras sub-intenciones específicas en el prompt, por lo que el LLM solo decidirá entre categorías generales. Si la entrada es un saludo informal (“hola”), el LLM podría no encajarlo claramente en “faq” (pregunta frecuente) y bien podría responder “n/a”. Si responde con una categoría válida (ej. “faq”), el clasificador usará esa categoría como intent principal
GitHub
. La sub-intención permanecerá vacía porque el LLM no proporciona el detalle de saludo/despedida.

Respuesta del clasificador: El servicio llm_docs-mcp retorna ya sea un string (modo flat) o un dict con intent/sub_intent/etc. (modo rich). Por defecto está en modo “flat” para compatibilidad
GitHub
GitHub
. En modo flat, hay una regla especial: si la intención principal es faq y la sub_intent es “saludo”, “despedida” o “agradecimiento”, en vez de devolver “faq” devuelve directamente la sub_intent (ej. “saludo”)
GitHub
. Esto está pensado para que saludos y despedidas se traten como intenciones independientes en el flujo viejo.

Orquestación y Respuesta: El orquestador recibe la intención clasificada. Tiene un mapa para traducir la intención nueva a la acción correspondiente del bot
GitHub
. Ejemplos:

"saludo" se mapea a la acción de saludo (enviar mensaje de bienvenida).

"despedida" a la acción de cierre de conversación.

Intenciones informativas como "faq", "documento" o "tramite" se mapean a "ask_document", que inicia la búsqueda de respuesta en la base de conocimiento.

"agenda" -> "init_scheduler" (flujo de agendar cita), "reclamo" -> "init_complaint" (flujo de reclamo).

Si la intención resultante es "saludo", el orquestador inmediatamente devuelve el mensaje de saludo predefinido sin siquiera consultar la base de conocimiento
GitHub
GitHub
. Para "ask_document", en cambio, llamará nuevamente al servicio RAG para buscar una respuesta en los documentos (vía doc-generar_respuesta_llm). Finalmente, si la intención quedó como "n/a" (no entendida), el orquestador activa el fallback general, devolviendo “Lo siento, no he entendido tu consulta. ¿Podrías reformularla?”
GitHub
.

Inconsistencias entre las Definiciones JSON y el Código

Al revisar los archivos JSON en services/llm_docs-mcp/documents y el código del IntentEngine, se encuentran discrepancias que explican por qué saludos simples no se reconocen adecuadamente:

Ubicación de las frases de ejemplo: En los JSON de FAQs, las frases típicas del usuario están bajo la clave user_says al mismo nivel que “pregunta” y “respuesta”
GitHub
. Sin embargo, el código del IntentEngine asume que esas frases podrían estar dentro de metadata (usa obj.metadata.get("user_says")) en la construcción de texto searchable
GitHub
. Como resultado, el vector de búsqueda no incluye las frases reales de saludo (“hola”, “buenos días”, etc.), sino solo los tags como “saludo”, “faq” que estaban en metadata. Esto reduce drásticamente la capacidad de hacer match exacto. Por ejemplo, para “hola”, el motor solo veía los tokens saludo, faq, etc., de los tags, sin darse cuenta de que “hola” estaba listada como ejemplo en el JSON.

Uso de alias vs. user_says: El IntentEngine prioriza los alias exactos para puntuar alto una intención
GitHub
. En documentos de “trámites” y “documentos” sí se utilizan campos alias con frases equivalentes (p.ej. en RAG-Horario_Comercio.json se enumeran alias como “a qué hora cierran los negocios” para asociarlos a un fragmento)
GitHub
. Pero en los FAQs de saludo/despedida no se usó el campo alias – en su lugar se puso user_says. Dado que el código no integró bien user_says en la búsqueda, no existe un alias que coincida exactamente con “hola”. Esto es una inconsistencia en la definición: la intención “saludo” carece de alias directo utilizable por el clasificador, a diferencia de otras categorías.

Etiquetas y subcategorías: En el JSON, la intención de saludo tiene metadata.subcategory = "saludo" y tags incluyendo “saludo”
GitHub
. El IntentEngine efectivamente reconoce sub-intenciones típicas buscando palabras clave en la consulta (KW_FAQ) para asignar un slot o sub_intent
GitHub
GitHub
. De hecho, el código de guess_slot verá “hola” en la pregunta y retornará “saludo” como subcategoría prevista
GitHub
GitHub
, lo cual aporta un pequeño puntaje extra vía boost_by_keywords. Sin embargo, esto no es suficiente si no hay un match de alias fuerte o buena cobertura de tokens para pasar el umbral.

Ejemplos faltantes o incompletos: Notamos que para “¿Cómo estás?” (un saludo común), el JSON no tiene esa frase exacta en user_says sin el prefijo “hola”. Solo existen variantes como “hola como te va hoy”
GitHub
. Esto significa que si el usuario solo pone “¿Cómo estás?” a secas, el IntentEngine no la asociará directamente con la subcategoría saludo. Tampoco hay una entrada de “pregunta frecuente” para “¿Cómo estás?” como tal. Esta omisión de un ejemplo claro hace que la consulta quede sin clasificar por RAG y dependa enteramente del fallback.

El Clasificador Híbrido y el Umbral de Confianza

La combinación RAG + LLM enfrenta aquí algunos problemas de calibración:

Puntaje bajo para saludos cortos: Para “Hola”, el IntentEngine generó candidatos de FAQ saludo pero con score ~0.06 (aproximadamente) debido a la lógica de solapamiento limitada mencionada. Ese score está muy por debajo del umbral 0.18
GitHub
GitHub
, por lo que el motor descarta la coincidencia y devuelve intent=n/a. Irónicamente, un saludo sencillo debería ser una de las intenciones más fáciles de reconocer, pero el umbral fijo lo impidió. Un posible problema es que 0.18 podría ser demasiado exigente para consultas de muy pocas palabras, donde el solapamiento de tokens siempre será bajo. (El código RAG de búsqueda de fragmentos sí ajusta dinámicamente el threshold de similitud para consultas ≤3 palabras, p.ej. 0.45 en vez de 0.5, pero esa lógica no se replicó en el IntentEngine de alta nivel.)

Fallback al LLM poco sensible a “saludo”: Al fallar RAG, se invocó el LLM para clasificación. Dado que el prompt no incluía “saludo” como categoría posible, el modelo tuvo que forzar “Hola” en una de las categorías dadas. Es probable que el LLM haya devuelto "n/a" también (lo consideró fuera de las opciones significativas) o quizás “faq”. Si devolvió “n/a”, el clasificador se quedó con intent=n/a definitivamente
GitHub
. Si hubiese devuelto “faq”, entonces intent habría sido "faq" sin sub_intent (pues rich["sub_intent"] seguía None). En modo flat, el clasificador normalmente reemplazaría faq+saludo por “saludo”
GitHub
, pero aquí no ocurrió por dos razones: primero, el LLM no identificó la subintención; segundo, hay un detalle técnico en cómo se llamó al clasificador (modo "plain" en lugar de "flat") que hizo que el resultado se devolviera como dict en vez de string, provocando que el orquestador ignorara el sub_intent detectado. En resumen, el fallback no “entendió” que “Hola” era un saludo porque solo decidió la categoría general (o ninguna), perdiendo la información de subcategoría.

Pérdida de la sub_intent en la integración: Relacionado a lo anterior, existe una inconsistencia en la llamada API doc-classify_intent_llm. Se invoca classify_intent_with_llm(..., mode='plain') en el gateway
GitHub
, lo que internamente no activa el modo flat esperado. En consecuencia, el servicio devolvió algo como {"intent": {"intent":"faq", "sub_intent":"saludo", ...}} en lugar de un simple "saludo". El cliente (LlmDocsClient.classify_intent) toma solo el campo intent principal de esa respuesta
GitHub
, descartando la sub_intent. Así, aunque el IntentEngine hubiera marcado sub_intent="saludo", el orquestador nunca lo vio. Esta es una inconsistencia entre el formato de intención en JSON/código y lo que el orquestador espera. En la práctica, el orquestador terminó viendo classification.intent = "faq" en vez de "saludo". Por eso mapeó a ask_document en vez de al flujo de saludo, desviando el comportamiento. Este es un bug técnico en la integración del flujo (modo plain vs flat) que contribuyó al problema.

Por qué la Intención "saludo" No se Detecta (Caso “Hola”)

Con lo anterior podemos elaborar una hipótesis concreta de lo ocurrido cuando el bot respondió por defecto “Lo siento, no he entendido...” ante “Hola” o “¿Cómo estás?”:

Fase RAG: El mensaje “Hola” fue comparado con las entradas FAQ. Las frases de ejemplo estaban en el JSON
GitHub
, pero el IntentEngine no las consideró directamente en la comparación por la discrepancia de user_says. Solo pudo inferir indirectamente que podría ser un saludo gracias a la lista de palabras clave (KW_FAQ). Esto le dio a la entrada de saludo un ligero puntaje, pero no suficiente. Ningún alias exacto coincidió (porque no había alias “hola”), y el solapamiento de tokens fue mínimo (“hola” vs “saludo/faq/session_start”). Resultado: score ~0.06 < 0.18, por lo que no clasificó la intención; devolvió intent: "n/a"
GitHub
.

Fallback LLM: Al no haber intención, el sistema preguntó al LLM. Es posible que el LLM haya devuelto “n/a” también (considerando “Hola” como fuera de las categorías de información) – esto calza con la respuesta final que vimos. Alternativamente, si devolvió “faq”, igualmente el sub_intent seguía vacío. En ambos casos, el clasificador no produjo “saludo” como salida final. Cabe destacar que incluso si RAG hubiera obtenido un poco más de score (digamos 0.2) y marcado faq+saludo, el bug de formato 'plain' vs 'flat' habría hecho que esa sub_intent “saludo” se pierda camino al orquestador. Por tanto, la consecuencia fue la misma: el orquestador no detectó que era un saludo.

Orquestador: Recibiendo intención "n/a" (o interpretando "faq" sin subintención relevante), aplica la ruta por defecto. No coincide con ninguno de los if explícitos (no es “saludo” ni “despedida” según su vista), entonces salta al manejo de fallback
GitHub
. Ahí devuelve la frase estándar de no comprensión: “Lo siento, no he entendido tu consulta. ¿Podrías reformularla?”, que es exactamente la respuesta que se observó.

Resumiendo la causa: una mezcla de datos y lógica. Por un lado, la intención “saludo” no estaba correctamente identificada por falta de alias o coincidencia directa (intención mal etiquetada y ejemplos incompletos como “¿Cómo estás?” omitido). Por otro lado, el clasificador híbrido tenía un umbral de confianza quizás demasiado alto para mensajes cortos y una implementación que ignoró las frases de entrenamiento, provocando un falso negativo. Y finalmente, hubo una desconexión entre el nombre de la intención en el clasificador y en el orquestador debido a la forma de retorno, impidiendo que el saludo activara su ruta especializada.

Recomendaciones para Solución y Pruebas

1. Corregir la gestión de frases de ejemplo (user_says/alias): Es crucial alinear el formato JSON con lo que espera el IntentEngine. Dos enfoques:

Modificar el código para que también considere obj.get("user_says") fuera de metadata. Por ejemplo, al cargar items, combinar las frases de user_says en la lista de alias o en el texto searchable. Así “hola” y similares estarían presentes para la comparación de tokens. Esto asegurará que consultas idénticas a ejemplos tengan puntajes altos (idealmente alias_score = 1.0 si se tratan como alias).

O modificar los JSON de FAQ para mover las frases a un campo alias. Actualmente alias está vacío en esas entradas
GitHub
, pero podría llenarse con “hola”, “buenos días”, etc., duplicando esencialmente lo de user_says. Con alias directos, el IntentEngine daría un score prácticamente perfecto cuando el usuario ingrese exactamente “hola” (porque alias_score vería coincidencia completa)
GitHub
. Cualquiera de las dos soluciones debe hacer que la intención saludo quede claramente identificada sin depender de heurísticas.

2. Incluir más variantes de saludo en los datos: Agregar “¿Cómo estás?” y “¿Cómo te va?” como frases de usuario para la intención de saludo. De hecho, la segunda entrada de saludo asume que el usuario siempre dice “hola” antes de preguntar cómo está el bot
GitHub
, lo cual no siempre sucederá. Incorporar estas variantes (quizás como alias adicionales) cubrirá el hueco. Asimismo, revisar si otras intenciones “simples” (agradecimientos, despedidas) tienen todas las variantes coloquiales posibles.

3. Ajustar el umbral de confianza del clasificador RAG: Evaluar si UMBRAL_SCORE = 0.18 es adecuado. Para frases de saludo de una palabra, es demasiado estricto. Se podría:

Bajar ligeramente el umbral global (ej. 0.1 – 0.15) para permitir matches con poca información pero alta probabilidad (como “hola” que en contexto debería mapear a saludo).

O aplicar un umbral dinámico: por ejemplo, si la consulta tiene ≤2 palabras, usar un umbral menor, similar a la lógica que se usa en la búsqueda de fragmentos (donde reducen threshold para consultas cortas).

Otra opción es otorgar un boost mayor en boost_by_keywords para subintenciones de saludo/despedida. Actualmente suma solo +0.06 si detecta “hola” y el item es subcategoría saludo
GitHub
. Subir ese boost (ej. a +0.15) aseguraría que “hola” supere el corte sin afectar mucho otras clasificaciones.

Cualquier cambio de umbral debe probarse cuidadosamente para no aumentar falsos positivos. Recomendación de prueba unitaria: crear inputs de una palabra o muy cortos (“hola”, “gracias”, “adiós”, “OK”) y verificar que ahora clasifican correctamente (saludo, agradecimiento, despedida, o incluso n/a si es algo irrelevante). Estos tests garantizarán que el nuevo umbral maneja bien casos de frontera.

4. Mejorar el fallback del LLM para intenciones conversacionales: Idealmente, al prompt de clasificación se le podrían añadir las subclases básicas (“saludo”, “despedida”, “agradecimiento”) como categorías reconocibles. Por ejemplo: “faq | documento | agenda | reclamo | tramite | saludo | despedida | n/a”. Así el modelo de lenguaje podría etiquetar “Hola” directamente como “saludo” en caso de que el RAG falle. No obstante, esto habría que hacerlo con cuidado para no confundir al modelo respecto a qué es “faq”. Otra idea es que si el IntentEngine devuelve n/a pero detectó ciertas palabras clave (como hola) en guess_slot, en vez de preguntarle al LLM se podría tomar esa pista para no descartar la intención. En todo caso, tras ajustar el RAG como en los pasos anteriores, quizá el fallback al LLM ni siquiera sea necesario para saludos.

5. Arreglar el formato de salida del clasificador en la API: A corto plazo, usar mode="flat" en la llamada doc-classify_intent_llm resolverá la pérdida de sub_intent. En modo flat, el servicio hubiera devuelto simplemente "saludo" en vez de un dict, y el orquestador lo habría mapeado correctamente al flujo de saludo
GitHub
GitHub
. Esta es una corrección sencilla en el gateway o en el cliente. Alternativamente, podrían modificar classify_intent_with_llm para que trate "plain" como sinónimo de "flat". De cualquier forma, es importante mantener la consistencia: lo que el orquestador espera (un intent string) vs. lo que el microservicio entrega.

Prueba recomendada: Simular una llamada a la API de clasificación (como lo haría el orquestador) con entradas de diferentes tipos y verificar que el resultado contiene la intent esperada. Por ejemplo, un test de integración que pase por LlmDocsClient.classify_intent("hola") y confirme que recibe intent: "saludo" en lugar de "faq" o "n/a". Asimismo, verificar que para consultas más complejas sigue comportándose correctamente (e.g., “Necesito agendar…” → intent “agenda”, etc., ya hay pruebas parametrizadas para eso).

6. Unit tests para flujos de saludo/despedida: Añadir casos específicos en la suite de tests que cubran saludos y despedidas. Por ejemplo:

Input "hola" debería producir una respuesta igual al GREETING_RESPONSE esperado (o al menos clasificar la intención como saludo antes del formateo de respuesta).

Input "adiós" debería desencadenar FAREWELL_RESPONSE.

Input con agradecimiento (“gracias”) debería quizás ser reconocido para un posible mensaje de “de nada” (si está contemplado).

También probar variantes como "Hola, ¿cómo estás?" juntas y separadas, asegurando que ninguna termine en el fallback genérico.

Estos tests de extremo a extremo (desde la clasificación hasta la respuesta final) ayudarán a garantizar que las intenciones simples de conversación ya no caigan en el default de “no entendido”.

En resumen, la intención “saludo” no se estaba detectando por una combinación de datos incompletos y detalles de implementación. Corrigiendo las etiquetas/alias en los JSON, ajustando la lógica de similitud (o el umbral) y arreglando la entrega de la intención al orquestador, el bot podrá reconocer “Hola” y “¿Cómo estás?” como saludos y responder apropiadamente con su mensaje de bienvenida
GitHub
 en lugar de pedir reformulación
GitHub
. Con las recomendaciones anteriores implementadas y cubiertas por pruebas unitarias, el flujo de clasificación será más robusto para estas interacciones sencillas.
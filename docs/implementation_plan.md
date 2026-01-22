# Análisis y Plan de Trabajo: Chatbot de Tránsito (Modelo Determinista)

## Resumen de la Sección (Checklist)
El objetivo de esta etapa es implementar un **Modelo Determinista** inicial para la vertical de **Tránsito**, diferenciando claramente entre dos tipos de interacciones ("Apps"):
1.  **FAQ / Informacional**: Preguntas sobre requisitos, documentos, costos, plazos de trámites (ej. Licencias).
2.  **Preguntas Complejas / Flujos**: Interacciones que requieren pasos o derivación a sistemas externos (ej. Agendamiento de horas, Reclamos).

El checklist destaca la importancia de:
-   **Routing Determinista**: Distinguir con alta precisión entre una pregunta de FAQ y una intención de Agendamiento.
-   **Base de Datos FAQ (KB)**: Estructurada, canónica y con fuentes validadas.
-   **Manejo de Respuestas**: Respuestas breves, institucionales y trazables.

## Análisis de Archivos JSON (`docs/Transito`)
Se han identificado los siguientes archivos fuente que alimentarán este modelo:

### 1. `001LIC_TRANS_LICENCIAS.json` (FAQ / Trámites)
-   **Contenido**: Información detallada sobre licencias (Obtención A1-A5, B, Renovación).
-   **Estructura**:
    -   `id`: Identificador único (ej. `liccond-obtencion_profesional`).
    -   `aliases`: Frases de búsqueda (ej. "sacar licencia profesional").
    -   `respuestas`: Campos estructurados (`donde`, `requisitos`, `documentacion`, `costos`, `plazos`, etc.).
    -   `bordes`: Límites de la respuesta (qué NO decir).
-   **Uso**: Fuente principal para el módulo FAQ. Se debe indexar por `aliases` y servir respuestas específicas basadas en la sub-pregunta (ej. "¿cuánto cuesta?" -> campo `costos`).

### 2. `002RESERV-HORA.json` (App Compleja: Agenda)
-   **Contenido**: Instrucciones para reservar hora.
-   **Estructura**: Similar a un trámite, pero con `tipo_atencion: "agenda_oficial"`.
-   **Respuestas Específicas**: `instrucciones_app` (pasos a seguir), `derivacion`.
-   **Uso**: Define la respuesta para la intención "Agendar". Actualmente actúa como una **derivación** (instruye al usuario cómo usar la app externa), pero el Router debe identificar esto como una "App distinta" (Agenda) para potencialmente iniciar un flujo más complejo en el futuro.

## Plan de Trabajo Propuesto

Avancemos en las siguientes tareas para cumplir con la etapa de "Modelo Determinista":

### Fase 1: Estructuración y Validación de Contenido (Prioridad)
Para responder a "¿Cómo lo harías?":

1.  **Definición de Esquema (Schema Definition)**:
    -   Crearemos interfaces estrictas (TypeScript/Zod) para `FAQItem` (Simples) y `ComplexFlowItem` (Complejas).
    -   **FAQ**: Debe tener `respuestas` con campos de contenido (`requisitos`, `plazos`, etc.).
    -   **Compleja**: Debe tener `tipo_atencion` definido y `instrucciones_app` o `derivacion`.

2.  **Validación Automática**:
    -   Crear un script de validación (`scripts/validate_knowledge_base.ts`) que recorra `docs/Transito/Json/*.json`.
    -   Verificar unicidad de `id`.
    -   Verificar que `aliases` no chocan entre sí (ambigüedad).

3.  **Generación de "Preguntas" (Dataset)**:
    -   Extraeremos todos los `aliases` de los JSONs para crear un `dataset_preguntas.json` plano.
    -   Este dataset servirá para entrenar/probar el Router Determinista.
    -   Estructura: `{ "utterance": "sacar licencia clase a", "label": "liccond-obtencion_profesional", "type": "faq" }`.

### Fase 2: Router Determinista (El "Cerebro" Inicial)
-   [ ] **Detector de Intención**:
    -   Implementar lógica para detectar si el usuario quiere "Información" (FAQ) o "Acción" (Agendar).
    -   Uso de `aliases` exactos o *fuzzy matching* simple para mapear input usuario -> `id_tramite`.
-   [ ] **Controlador de Flujo**:
    -   Si es FAQ (`001...`): Extraer la respuesta correspondiente (ej. si pregunta "requisitos de licencia A1", devolver `respuestas.requisitos`).
    -   Si es Agenda (`002...`): Devolver la respuesta de `instrucciones_app` o `derivacion`.

### Fase 3: Formato de Respuesta
-   [ ] **Standardizer**: Asegurar que todas las respuestas sigan el "tono institucional" definido:
    -   Citar fuente (campo `fuentes`).
    -   Respetar "bordes" (no inventar info).
    -   Incluir "Aspect Buttons" sugeridos (ej. "Ver costos", "Agendar") para guiar la conversación.

## Próximos Pasos Inmediatos
1.  Confirmar si la estructura de los JSON actuales es definitiva para esta etapa.
2.  Comenzar con la implementación del **Loader** y el **Router básico** usando estos dos archivos como piloto.

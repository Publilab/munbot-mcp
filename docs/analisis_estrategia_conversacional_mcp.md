# Análisis Completo de Estrategia Conversacional MCP

## 📋 Resumen Ejecutivo

El documento presenta una estrategia integral para transformar un chatbot actual basado en MCP y lógica if-else en un sistema conversacional avanzado que aprovecha **mistral-7b-instruct** como motor LLM principal, manteniendo la confiabilidad del sistema estructurado actual.

### Objetivo Principal
Implementar un **orquestador híbrido inteligente** que combine:
- La precisión del sistema basado en reglas para tareas críticas
- La fluidez conversacional del LLM para interacciones naturales
- Un sistema de fallback multicapa robusto

---

## 🏗️ Arquitectura Propuesta

### Componentes Principales del Sistema Híbrido

#### 1. **MCP Hybrid Orchestrator** (`orchestrator.py`)
- **Función**: Punto de entrada central para todas las consultas
- **Responsabilidad**: Orquestar el flujo conversacional completo
- **Tecnología**: FastAPI con integración Redis y PostgreSQL

#### 2. **Decision Layer** (Impulsado por `mistral-7b-instruct`)
Analiza cada entrada del usuario para extraer:
- **Intención**: Objetivo del usuario
- **Entidades**: Datos clave (números de pedido, fechas, etc.)
- **Confianza**: Nivel de certeza del análisis (0-1)
- **Sentimiento**: Estado emocional del usuario

#### 3. **Rule-Based System** (Servidores MCP)
- **complaints-mcp**: Gestión de reclamos y denuncias
- **scheduler-mcp**: Sistema de agendamiento de citas
- **llm_docs-mcp**: Consulta de documentación municipal

#### 4. **LLM Generation Engine** (`mistral-7b-instruct`)
- Genera respuestas conversacionales fluidas
- Maneja preguntas abiertas y charlas informales
- Actúa como fallback cuando el sistema de reglas no tiene respuesta

#### 5. **Response Engine**
- Unifica respuestas de reglas y LLM
- Entrega respuesta final al usuario
- Actualiza el contexto conversacional

---

## 🔄 Flujos de Datos Conversacionales

### Flujo General
1. **Recepción**: Usuario envía mensaje → `orchestrator.py`
2. **Análisis**: Decision Layer (mistral-7b-instruct) analiza intención
3. **Evaluación**: Orquestador evalúa confianza y enruta:
   - **Alta confianza + intención conocida** → Servidor MCP específico
   - **Confianza media + intención conversacional** → LLM Generation Engine
   - **Baja confianza** → Sistema de fallback
4. **Procesamiento**: Motor seleccionado procesa la solicitud
5. **Respuesta**: Response Engine envía respuesta final
6. **Actualización**: Contexto conversacional actualizado

### Flujos Específicos por Microservicio

#### **complaints-mcp** (Gestión de Reclamos)
```
Usuario: "Necesito poner un reclamo"
├── Análisis de intención → complaint-registrar_reclamo
├── Recolección de datos personales (con validación)
├── Captura del contenido del reclamo
├── Registro en base de datos
├── Confirmación visual al usuario
├── Envío por email
└── Despedida
```

#### **scheduler-mcp** (Agendamiento)
```
Usuario: "Necesito pedir una hora de atención"
├── Análisis de intención → scheduler-appointment_create
├── Consulta de horarios disponibles
├── Presentación de opciones al usuario
├── Recolección de datos personales
├── Reserva y registro
├── Confirmación visual
├── Envío por email
└── Despedida
```

#### **llm_docs-mcp** (Documentación)
Dos funciones principales:
1. **Búsqueda en documentos**: Consulta específica en documentos municipales
2. **Generación conversacional**: Evita fallback para consultas dentro del contexto

---

## 💻 Requisitos Técnicos por Microservicio

### **mcp-core** (Orquestador Principal)

#### Dependencias Técnicas
```python
- FastAPI (API framework)
- psycopg2 (PostgreSQL)
- redis (gestión de sesiones)
- requests (comunicación entre servicios)
- pydantic (validación de datos)
```

#### Configuración de Servicios
```python
MICROSERVICES = {
    "complaints-mcp": "http://complaints-mcp:7000/tools/call",
    "llm_docs-mcp": "http://llm-docs-mcp:8000/tools/call", 
    "scheduler-mcp": "http://scheduler-mcp:6001/tools/call"
}
```

#### Campos Requeridos por Tool
```python
REQUIRED_FIELDS = {
    "complaint-registrar_reclamo": ["nombre", "mail", "mensaje", "categoria", "departamento"],
    "scheduler-appointment_create": ["usu_name", "usu_mail", "usu_whatsapp", "fecha", "hora", "motiv"]
}
```

### **Integración con Mistral-7B-Instruct**

#### Configuración de API
```python
api_url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"
headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
parameters = {
    "max_new_tokens": 256,
    "temperature": 0.7,
    "return_full_text": False
}
```

#### Análisis de Intención
El LLM analiza cada consulta para identificar:
- `complaint-registrar_reclamo`
- `doc-buscar_fragmento_documento`
- `doc-generar_respuesta_llm`
- `scheduler-reservar_hora`
- `scheduler-appointment_create`
- `scheduler-listar_horas_disponibles`
- `scheduler-cancelar_hora`
- `scheduler-confirmar_hora`

### **Gestión de Datos**

#### PostgreSQL (Persistencia)
- **Base de datos**: `munbot`
- **Tabla principal**: `conversaciones_historial`
- **Esquemas**: `appointments.sql`, `reclamos.sql`

#### Redis (Sesiones)
- **Host**: `redis:6379`
- **Función**: Memoria conversacional a corto plazo
- **Expiración**: 3600 segundos (1 hora)

---

## 💬 Gestión de Contexto y Mejores Prácticas Conversacionales

### **ConversationalContextManager**

#### Características Principales
```python
class ConversationalContextManager:
    - Memoria a corto plazo: Redis (sesiones activas)
    - Memoria a largo plazo: Base de datos vectorial
    - Expiración de sesión: 1 hora
    - Límite de historial: 10 intercambios por sesión
```

#### Funciones Clave
- `get_context(session_id)`: Recupera historial conversacional
- `update_context(session_id, user_query, bot_response)`: Actualiza contexto
- `get_history_as_string(history)`: Formatea para prompts LLM

### **Personalización de Respuestas**
```python
async def generate_personalized_response(user_query: str, context: dict):
    user_name = context.get("user_profile", {}).get("name", "cliente")
    personalized_prompt = f"Dirígete al usuario como {user_name}. {base_prompt}"
```

### **Gestión de Prompts**
- **Ubicación**: `/prompts/` directory
- **Esquemas de herramientas**: `/tool_schemas/`
- **Plantillas contextuales**: Reemplazo de variables `{{variable}}`

---

## 🛡️ Sistema de Fallback Multicapa

### **Nivel 1: Detección de Confianza**
```python
if confidence < HYBRID_CONFIDENCE_THRESHOLD:
    # Análisis de sentimiento para detectar frustración
    sentiment = intent_analysis.get("sentiment", "neutral")
    
    if sentiment == "very_negative":
        # Escalación inmediata a humano
        escalation_client.handle({"reason": "User frustration detected"})
```

### **Nivel 2: Respuesta Generativa Segura**
```python
# El LLM explica que no entendió pero intenta ayudar
response = llm_client.generate_safe_fallback_response(user_query, context)
```

### **Nivel 3: Escalación Progresiva**
```python
fallback_count = context_manager.get_fallback_count(session_id)
if fallback_count >= 3:
    # Escalación a humano tras múltiples fallos
    escalation_client.handle({"reason": "Repeated understanding failures"})
```

### **Estrategias de Recuperación**
- **Análisis de sentimiento**: Detección de frustración del usuario
- **Conteo de fallbacks**: Escalación automática tras 3 intentos
- **Logging detallado**: Registro de eventos para análisis posterior

---

## 📊 Monitoreo y KPIs

### **Métricas Clave de Rendimiento**

#### 1. **Tasa de Resolución en Primer Contacto (FCR)**
- **Cálculo**: `% consultas resueltas sin fallback / Total consultas`
- **Meta**: >85%

#### 2. **Tasa de Comprensión de Intención**
- **Cálculo**: `(Llamadas MCP + LLM alta confianza) / Total consultas`
- **Meta**: >90%

#### 3. **Tasa de Fallback**
- **Cálculo**: `Activaciones de fallback / Total consultas`
- **Meta**: <10%

#### 4. **Puntuación de Satisfacción (CSAT)**
- **Escala**: 1-5
- **Meta**: >4.0

#### 5. **Duración Promedio de Conversación**
- **Medición**: Turnos conversacionales o tiempo total
- **Objetivo**: Reducción progresiva manteniendo calidad

### **Herramientas de Análisis**
- **Dashboard**: Grafana/Kibana/Looker para visualización en tiempo real
- **Logging detallado**: Registro de decisiones del orquestador
- **Análisis de conversaciones fallidas**: Sistema de marcado y revisión manual

### **Proceso de Mejora Continua**
1. **Recopilación**: Logs y métricas de rendimiento
2. **Análisis semanal**: Revisión de conversaciones fallidas y KPIs
3. **Refinamiento de prompts**: Ingeniería iterativa de prompts
4. **Nuevas reglas**: Creación de servidores MCP para patrones identificados

---

## 🔧 Clases y Componentes Técnicos Principales

### **Modelos de Datos (Pydantic)**
```python
class OrchestratorInput(BaseModel)           # Entrada del orquestador
class ComplaintModel(BaseModel)              # Modelo de reclamos
class ComplaintOut(BaseModel)                # Salida de reclamos  
class AppointmentCreate(BaseModel)           # Creación de citas
class AppointmentOut(AppointmentCreate)      # Salida de citas
class AppointmentConfirm(BaseModel)          # Confirmación de citas
class AppointmentCancel(BaseModel)           # Cancelación de citas
```

### **Componentes de Procesamiento**
```python
class HybridOrchestrator                     # Orquestador principal híbrido
class MistralClient                          # Cliente para mistral-7b-instruct
class ConversationalContextManager          # Gestor de contexto conversacional
class ComplaintRepository                    # Repositorio de reclamos
class IPWhitelistMiddleware                  # Middleware de seguridad
```

### **Funciones Utilitarias Clave**
```python
def detect_intent_llm(user_input: str)      # Detección de intención con LLM
def detect_intent_keywords(user_input: str) # Fallback basado en palabras clave
def route_to_service(tool: str)             # Enrutamiento a microservicios
def validate_against_schema(data, schema)   # Validación de esquemas
def call_tool_microservice(tool, params)    # Llamadas a microservicios
def lookup_faq_respuesta(pregunta: str)     # Búsqueda en FAQ
```

---

## ✅ Cumplimiento de Criterios de Éxito

### ✅ **Extracción completa de la arquitectura propuesta**
- Arquitectura híbrida con orquestador inteligente claramente definida
- 5 componentes principales identificados y documentados
- Flujo de datos completo mapeado

### ✅ **Identificación de todos los componentes del sistema híbrido**
- **mcp-core**: Orquestador principal con FastAPI
- **complaints-mcp**: Gestión de reclamos (puerto 7000)
- **scheduler-mcp**: Sistema de agendamiento (puerto 6001)  
- **llm_docs-mcp**: Consulta de documentación (puerto 8000)
- **mistral-7b-instruct**: Motor LLM vía HuggingFace API

### ✅ **Análisis detallado de los flujos conversacionales específicos**
- Flujo general de 6 pasos documentado
- Flujos específicos por microservicio detallados
- Lógica de enrutamiento basada en confianza e intención

### ✅ **Documentación de los requisitos técnicos por microservicio**
- Dependencias Python identificadas (FastAPI, psycopg2, redis, etc.)
- Configuración de servicios y puertos
- Campos requeridos por herramienta especificados
- Estructura de base de datos documentada

### ✅ **Identificación de las mejores prácticas conversacionales propuestas**
- Gestión de contexto con memoria a corto y largo plazo
- Personalización de respuestas con información del usuario
- Sistema de prompts templatable con variables
- Limitación de historial conversacional (10 intercambios)

### ✅ **Análisis de la gestión de contexto y fallbacks**
- **Contexto**: Redis para sesiones activas, BD vectorial para persistencia
- **Fallbacks multicapa**: 3 niveles de escalación progresiva
- **Detección de frustración**: Análisis de sentimiento integrado
- **Recuperación automática**: Escalación tras 3 intentos fallidos

---

## 🎯 Conclusiones y Recomendaciones

### **Fortalezas de la Estrategia**
1. **Arquitectura bien estructurada** con separación clara de responsabilidades
2. **Enfoque híbrido equilibrado** entre confiabilidad y conversacionalidad
3. **Sistema de fallback robusto** con múltiples capas de recuperación
4. **Monitoreo integral** con KPIs específicos y medibles
5. **Escalabilidad modular** que permite agregar nuevos microservicios

### **Implementación Progresiva Recomendada**
1. **Fase 1**: Implementar orquestador híbrido básico
2. **Fase 2**: Integrar gestión de contexto conversacional
3. **Fase 3**: Activar sistema de fallback multicapa  
4. **Fase 4**: Implementar monitoreo y análisis de KPIs
5. **Fase 5**: Optimización continua basada en datos

### **Consideraciones Técnicas Críticas**
- **Latencia**: Optimizar llamadas a API de Mistral para respuesta rápida
- **Escalabilidad**: Configurar Redis cluster para alta disponibilidad
- **Seguridad**: Implementar autenticación robusta para APIs externas
- **Costos**: Monitorear uso de tokens de Mistral para control de gastos

La estrategia propuesta representa una evolución natural y bien planificada del sistema actual hacia una experiencia conversacional más rica y humana, manteniendo la confiabilidad operacional existente.

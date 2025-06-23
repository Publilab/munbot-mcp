# Registro de Preguntas sin Respuesta y Feedback

Este documento describe brevemente las tablas incorporadas para almacenar preguntas no contestadas y el feedback de los usuarios.

## preguntas_no_contestadas
- **id**: identificador autoincremental.
- **texto_pregunta**: pregunta original enviada por el usuario.
- **fecha_hora**: marca temporal de registro.
- **usuario_id**: identificador del usuario si se dispone.
- **intent_detectada**: intención detectada por el orquestador (por defecto `unknown`).
- **respuesta_dada**: mensaje que envió el bot.
- **canal**: canal de origen (opcional).

## feedback_usuario
- **id**: clave primaria.
- **pregunta_id**: referencia opcional a `preguntas_no_contestadas`.
- **feedback_texto**: texto que envió el usuario al valorar la respuesta.
- **fecha_hora**: momento de registro.
- **usuario_id**: identificador opcional del usuario.

Estas tablas permiten analizar con mayor detalle en qué temas falla el bot y recopilar comentarios directos de los usuarios para mejorar continuamente el sistema.

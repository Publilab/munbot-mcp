# `complaints-api`
## Gestión de Reclamos y Denuncias
Un microservicio independiente para recopilar, clasificar y gestionar reclamos y denuncias, integrado en una arquitectura de microservicios escalable.
### Características Principales
* Independencia funcional : Ejecuta una tarea específica (gestión de reclamos) sin depender de otros componentes .
* Escalabilidad : Diseñado para crecer horizontalmente, adaptándose a cargas variables.
* Comunicación vía API : Interactúa con otros servicios (como chatbots o dashboards) mediante endpoints claros.
* Clasificación automática : Asigna reclamos a departamentos (seguridad, obras, etc.) según keywords en la descripción.
* Seguimiento único : Genera IDs para cada reclamo y notifica al usuario vía email.
### Instalación y Uso
### Requisitos
* Docker y Docker Compose instalados.
* Variables de entorno configuradas (ej: credenciales de email).
### Ejecución
1. Clona el repositorio:
```bash
 git clone https://github.com/your-repo/complaints-api.git

```

2. Inicia el contenedor:bash
```bash

docker-compose up --build

```
 3. Accede al servicio en **http://localhost:7000**.

### Uso Básico
### Endpoint Principal
POST `/complaint` *Parámetros requeridos* :
```json

{  
  "nombre_denunciante": "string",  
  "mail": "string",  
  "mensaje": "string",  
  "categoria": 1 ó 2 (1=reclamo, 2=denuncia),  
  "departamento": 1, 2, 3 ó 4 (seguridad, obras, ambiente, otro)  
}

```
  
*Respuesta* :
```json

{  
  "message": "Reclamo registrado",  
  "id_seguimiento": "uuid-aleatorio"  
}

```
### Contribución
* Estructura : El código sigue estándares de microservicios (independencia y modularidad).
* Contribuciones :
  1. Crea una rama nueva: **git checkout -b feature/nombre**.
  2. Realiza tus cambios y test.
  3. Abre un Pull Request.
### Licencia
Distribuido bajo la licencia MIT. Consulta **LICENSE** para más detalles.
.

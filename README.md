
# 🍰 G&A Sweet Dreams

## Sistema de gestión de productos de pastelería

G&A Sweet Dreams es una aplicación web desarrollada con Python, Flask y MongoDB que permite administrar un catálogo de productos de pastelería mediante operaciones CRUD.

El sistema cuenta con una interfaz elegante en tonos rosa y dorado, diseñada para facilitar el registro, la consulta, la edición y la eliminación de productos.

## Objetivo

Desarrollar una aplicación web funcional que implemente operaciones CRUD y una API REST utilizando Flask y MongoDB, con publicación del código en GitHub y despliegue en Render.

## Tecnologías utilizadas

- Python
- Flask
- MongoDB Atlas
- PyMongo
- HTML5
- CSS3
- Bootstrap 5
- Git y GitHub
- Render
- Gunicorn

## Funcionalidades principales

El sistema permite:

1. Registrar nuevos productos de pastelería.
2. Consultar los productos registrados.
3. Editar la información de un producto.
4. Eliminar productos del catálogo.
5. Consultar y administrar productos mediante una API JSON.

## Información de los productos

Cada producto contiene tres campos principales:

| Campo | Descripción |
|---|---|
| nombre | Nombre del producto |
| categoria | Tipo de postre |
| estado | Disponibilidad del producto |

### Categorías disponibles

- Pasteles
- Cupcakes
- Galletas
- Cheesecakes
- Macarons
- Postres especiales

### Estados disponibles

- Disponible
- Agotado
- Por encargo

## Base de datos

El proyecto utiliza MongoDB Atlas.

- Base de datos: `ga_sweet_dreams`
- Colección: `productos`

MongoDB asigna automáticamente un identificador único a cada producto.

## API REST

La aplicación incluye los siguientes endpoints:

| Método | Endpoint | Función |
|---|---|---|
| GET | `/api/productos` | Consultar todos los productos |
| GET | `/api/productos/<id>` | Consultar un producto |
| POST | `/api/productos` | Registrar un producto |
| PUT | `/api/productos/<id>` | Actualizar un producto |
| DELETE | `/api/productos/<id>` | Eliminar un producto |

### Ejemplo de JSON

```json
{
  "nombre": "Pastel de chocolate",
  "categoria": "Pasteles",
  "estado": "Disponible"
}
```

## Instalación local

### 1. Clonar el repositorio

```bash
git clone https://github.com/gisleytomate/GA-Sweet-Dreams.git
cd GA-Sweet-Dreams
```

### 2. Crear un entorno virtual

```bash
python -m venv .venv
```

En Windows, activar el entorno:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar MongoDB

Crear la variable de entorno `MONGO_URI` con una cadena de conexión válida de MongoDB Atlas.

No incluir contraseñas ni credenciales en el repositorio.

### 5. Ejecutar la aplicación

```bash
python app.py
```

Abrir en el navegador:

http://127.0.0.1:5000

## Publicación

El código fuente está alojado en GitHub y la aplicación se encuentra desplegada en Render mediante un servicio web de Python.

La conexión con MongoDB Atlas se configura utilizando la variable de entorno `MONGO_URI`.

## Pruebas realizadas

Se comprobaron satisfactoriamente:

- Registro de productos.
- Consulta del catálogo.
- Edición de productos.
- Eliminación de productos.
- API JSON con GET, POST, PUT y DELETE.
- Conexión con MongoDB Atlas.
- Publicación en GitHub.
- Funcionamiento de la aplicación en Render.

## Conclusión

G&A Sweet Dreams demuestra la integración de una aplicación web Flask con una base de datos NoSQL. El proyecto permite administrar productos de pastelería mediante una interfaz gráfica y una API REST, aplicando conocimientos de programación web, bases de datos, control de versiones y despliegue en la nube.

## Repositorio

https://github.com/gisleytomate/GA-Sweet-Dreams

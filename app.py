
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify, session
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from datetime import datetime
import os
from dotenv import load_dotenv
import hmac
import secrets
from functools import wraps
from werkzeug.security import check_password_hash

import uuid
from io import BytesIO
from PIL import Image, UnidentifiedImageError

from urllib.parse import quote



# Cargar variables privadas desde el archivo .env
load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]


# WhatsApp oficial de G&A Sweet Dreams
WHATSAPP_NEGOCIO = "529984925174"



# Configuración de fotografías de productos
app.config["UPLOAD_FOLDER"] = os.path.join(
    app.root_path, "static", "uploads"
)

# Tamaño máximo de solicitud: 5 MB
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# Formatos de imagen permitidos
EXTENSIONES_PERMITIDAS = {"jpg", "jpeg", "png", "webp"}

# Crear la carpeta de fotografías si no existe
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def guardar_imagen(archivo):
    if not archivo or not archivo.filename:
        raise ValueError("Selecciona una fotografía del postre.")

    extension = archivo.filename.rsplit(".", 1)[-1].lower()

    if extension not in EXTENSIONES_PERMITIDAS:
        raise ValueError("Solo se permiten imágenes JPG, PNG o WEBP.")

    try:
        archivo.stream.seek(0)

        with Image.open(archivo.stream) as imagen:
            imagen.verify()

        archivo.stream.seek(0)

        with Image.open(archivo.stream) as imagen:
            formato = imagen.format

        formatos_validos = {
            "JPEG": "jpg",
            "PNG": "png",
            "WEBP": "webp"
        }

        if formato not in formatos_validos:
            raise ValueError("El archivo no es una imagen permitida.")

        nombre = f"{uuid.uuid4().hex}.{formatos_validos[formato]}"
        ruta = os.path.join(app.config["UPLOAD_FOLDER"], nombre)

        archivo.stream.seek(0)
        archivo.save(ruta)

        return nombre

    except (UnidentifiedImageError, OSError):
        raise ValueError("La fotografía seleccionada no es válida.")





# Protección CSRF para formularios
@app.context_processor
def agregar_token_csrf():
    def csrf_token():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_hex(32)
        return session["_csrf_token"]

    return {"csrf_token": csrf_token}



@app.before_request
def verificar_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        
        if request.path.startswith("/api/") and not session.get("es_admin"):
            return jsonify({
                "error": "Debes iniciar sesión como administrador."
                }), 401

        token_guardado = session.get("_csrf_token", "")

        if request.path.startswith("/api/"):
            token_enviado = request.headers.get(
                "X-CSRF-Token", ""
            )
        else:
            token_enviado = request.form.get(
                "_csrf_token", ""
            )

        if not token_guardado or not secrets.compare_digest(
            token_guardado, token_enviado
        ):
            abort(400, description="Token de seguridad inválido.")



# Credenciales del administrador
ADMIN_USUARIO = os.environ.get("ADMIN_USUARIO")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")


def administrador_requerido(funcion):
    @wraps(funcion)
    def protegida(*args, **kwargs):
        if not session.get("es_admin"):
            if request.path.startswith("/api/"):
                return jsonify({
                    "error": "Debes iniciar sesión como administrador."
                }), 401

            return redirect(url_for("login"))

        return funcion(*args, **kwargs)

    return protegida


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("es_admin"):
        return redirect(url_for("administracion"))

    error = None

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("contrasena", "")

        usuario_valido = (
            ADMIN_USUARIO is not None
            and hmac.compare_digest(usuario, ADMIN_USUARIO)
        )

        contrasena_valida = (
            ADMIN_PASSWORD_HASH is not None
            and check_password_hash(
                ADMIN_PASSWORD_HASH,
                contrasena
            )
        )

        if usuario_valido and contrasena_valida:
            session.clear()
            session["es_admin"] = True
            return redirect(url_for("administracion"))

        error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@administrador_requerido
def logout():
    session.clear()
    return redirect(url_for("inicio"))


# Base de datos independiente
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/")
cliente = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
base_datos = cliente["ga_sweet_dreams"]
productos = base_datos["productos"]
pedidos = base_datos["pedidos"]



CATEGORIAS = [
    "Pasteles",
    "Cupcakes",
    "Galletas",
    "Cheesecakes",
    "Macarons",
    "Postres especiales"
]

ESTADOS = ["Disponible", "Agotado", "Por encargo"]


def validar_producto(datos):
    nombre = str(datos.get("nombre") or "").strip()
    categoria = str(datos.get("categoria") or "").strip()
    estado = str(datos.get("estado") or "").strip()
    
    from decimal import Decimal, InvalidOperation
    try:
        precio = Decimal(str(datos.get("precio") or "").strip())
    except InvalidOperation:
        raise ValueError("Ingresa un precio válido.")
    
    if not precio.is_finite() or precio <= 0:
        raise ValueError("El precio debe ser mayor que cero.")
    
    if precio.as_tuple().exponent < -2:
        raise ValueError("El precio debe tener máximo dos decimales.")
    precio_centavos = int(precio * 100)

    if not nombre or not categoria or not estado:
        raise ValueError("Completa nombre, categoría y estado.")

    if len(nombre) > 80:
        raise ValueError("El nombre no puede superar 80 caracteres.")

    if categoria not in CATEGORIAS:
        raise ValueError("Selecciona una categoría válida.")

    if estado not in ESTADOS:
        raise ValueError("Selecciona un estado válido.")

    return {
        "nombre": nombre,
        "categoria": categoria,
        "estado": estado,
        "precio_centavos": precio_centavos
        }


def leer_formulario():
    try:
        return validar_producto(request.form)
    except ValueError as error:
        abort(400, description=str(error))


def buscar_producto(id):
    if not ObjectId.is_valid(id):
        abort(404, description="El ID del producto no es válido.")

    producto = productos.find_one({"_id": ObjectId(id)})

    if producto is None:
        abort(404, description="El producto no existe.")

    return producto


def convertir_json(producto):
    return {
        "id": str(producto["_id"]),
        "nombre": producto["nombre"],
        "categoria": producto["categoria"],
        "estado": producto["estado"]
    }



# Página de bienvenida
@app.route("/")
def inicio():
    destacados = list(
        productos.find({"estado": "Disponible"}).limit(4)
    )
    return render_template(
        "inicio.html",
        destacados=destacados
    )


# Catálogo público de la pastelería
@app.route("/catalogo")
def catalogo():
    lista = list(productos.find().sort("nombre", 1))
    return render_template(
        "catalogo.html",
        productos=lista,
        categorias=CATEGORIAS,
        estados=ESTADOS
    )


# Agregar un producto al carrito
@app.route("/carrito/agregar/<id>", methods=["POST"])
def agregar_carrito(id):
    producto = buscar_producto(id)

    # Solo permitir productos disponibles y con precio
    if producto.get("estado") != "Disponible":
        abort(400, description="Este producto no está disponible.")

    if not isinstance(producto.get("precio_centavos"), int):
        abort(400, description="Este producto todavía no tiene precio.")

    # Obtener el carrito de la sesión
    carrito = session.get("carrito", {})        
    
    
    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except (ValueError, TypeError):
        abort(400, description="Cantidad no válida.")

    if not 1 <= cantidad <= 99:
        abort(400, description="Selecciona entre 1 y 99 unidades.")

    # Agregar la cantidad al carrito
    carrito[id] = min(carrito.get(id, 0) + cantidad, 99)

    # Guardar los cambios
    session["carrito"] = carrito

    return redirect(url_for("catalogo"))



# Modificar cantidades del carrito
@app.route("/carrito/actualizar/<id>", methods=["POST"])
def actualizar_carrito(id):
    carrito = session.get("carrito", {})

    # Verificar que el producto esté en el carrito
    if id not in carrito:
        abort(404, description="Producto no encontrado en el carrito.")

    # Obtener la acción solicitada
    accion = request.form.get("accion")

    if accion not in ("aumentar", "disminuir", "eliminar"):
        abort(400, description="Acción no válida.")

    # Validar la cantidad actual
    try:
        cantidad = int(carrito[id])
    except (ValueError, TypeError):
        abort(400, description="Cantidad no válida.")

    if not 1 <= cantidad <= 99:
        abort(400, description="Cantidad fuera del rango permitido.")

    if accion == "aumentar":
        if cantidad < 99:
            carrito[id] = cantidad + 1

    elif accion == "disminuir":
        if cantidad > 1:
            carrito[id] = cantidad - 1
        else:
            carrito.pop(id, None)

    elif accion == "eliminar":
        carrito.pop(id, None)

    # Guardar el carrito actualizado
    session["carrito"] = carrito

    return redirect(url_for("ver_carrito"))



@app.route("/carrito/vaciar", methods=["POST"])
def vaciar_carrito():
    session.pop("carrito", None)
    return redirect(url_for("ver_carrito"))


# Mostrar el carrito de compras
@app.route("/carrito")
def ver_carrito():
    carrito = session.get("carrito", {})
   

    productos_carrito = []
    total_centavos = 0

    for id_producto, cantidad in carrito.items():
        try:
            cantidad = int(cantidad)

            if cantidad < 1 or cantidad > 99:
                continue

            producto = productos.find_one({
                "_id": ObjectId(id_producto)
            })
            


            if not producto:
                continue

            precio = producto.get("precio_centavos")

            if (
                producto.get("estado") != "Disponible"
                or not isinstance(precio, int)
                or precio <= 0
            ):
                continue

            subtotal = precio * cantidad
            total_centavos += subtotal

            productos_carrito.append({
                "id": str(producto["_id"]),
                "nombre": producto["nombre"],
                "cantidad": cantidad,
                "precio_centavos": precio,
                "subtotal_centavos": subtotal,
                "imagen": producto.get("imagen")
            })

        except (ValueError, TypeError):
            continue

    return render_template(
        "carrito.html",
        productos_carrito=productos_carrito,
        total_centavos=total_centavos
    )
    

# Página para finalizar la compra
@app.route("/checkout", methods=["GET"])
def checkout():
    carrito = session.get("carrito", {})

    # Evitar que el cliente compre con el carrito vacío
    if not carrito:
        return redirect(url_for("ver_carrito"))

    return render_template("checkout.html")


# Guardar el pedido del cliente
@app.route("/checkout/finalizar", methods=["POST"])
def finalizar_pedido():
    from datetime import datetime, timezone
    import re

    carrito = session.get("carrito", {})

    if not isinstance(carrito, dict) or not carrito:
        return redirect(url_for("ver_carrito"))

    # Datos del formulario
    nombre = request.form.get("nombre", "").strip()
    telefono = request.form.get("telefono", "").strip()
    direccion = request.form.get("direccion", "").strip()
    fecha_entrega = request.form.get("fecha_entrega", "").strip()
    observaciones = request.form.get("observaciones", "").strip()

    # Validar datos del cliente
    if not (
        1 <= len(nombre) <= 100
        and 1 <= len(direccion) <= 300
        and len(observaciones) <= 500
        and re.fullmatch(r"\+?[0-9\s()-]{10,20}", telefono)
    ):
        abort(400, description="Revisa los datos del cliente.")

    try:
        fecha_solicitada = datetime.strptime(
            fecha_entrega, "%Y-%m-%d"
        ).date()
    except ValueError:
        abort(400, description="Fecha de entrega no válida.")

    if fecha_solicitada < datetime.now().date():
        abort(400, description="La fecha no puede ser anterior a hoy.")

    # Preparar los productos del pedido
    detalle = []
    total_centavos = 0

    for id_producto, cantidad in carrito.items():
        try:
            cantidad = int(cantidad)
            identificador = ObjectId(id_producto)
        except (ValueError, TypeError):
            abort(400, description="Producto o cantidad no válida.")

        if not 1 <= cantidad <= 99:
            abort(400, description="Cantidad no válida.")

        producto = productos.find_one({"_id": identificador})

        if not producto:
            abort(400, description="Un producto ya no existe.")

        precio = producto.get("precio_centavos")

        if (
            producto.get("estado") != "Disponible"
            or type(precio) is not int
            or precio <= 0
        ):
            abort(400, description="Un producto ya no está disponible.")

        subtotal = precio * cantidad
        total_centavos += subtotal

        detalle.append({
            "producto_id": str(producto["_id"]),
            "nombre": producto["nombre"],
            "cantidad": cantidad,
            "precio_centavos": precio,
            "subtotal_centavos": subtotal
        })

    if not detalle:
        return redirect(url_for("ver_carrito"))
    
    
    # Crear un código privado para consultar el pedido
    token_pedido = secrets.token_urlsafe(32)


    # Documento que se guardará en MongoDB
    nuevo_pedido = {     
        "token_confirmacion": token_pedido,
        "nombre": nombre,
        "telefono": telefono,
        "direccion": direccion,
        "fecha_entrega": fecha_entrega,
        "observaciones": observaciones,
        "productos": detalle,
        "total_centavos": total_centavos,
        "estado": "Pendiente",
        
        "mensaje_admin": "",
        "fecha_actualizacion": None,

        "fecha_registro": datetime.now(timezone.utc)
        
    }

    resultado = pedidos.insert_one(nuevo_pedido)
    
    # Guardar una referencia privada al pedido en la sesión
    mis_pedidos = session.get("mis_pedidos", [])
    
    mis_pedidos.append({
        "id": str(resultado.inserted_id),
        "token": token_pedido
        })
    
    session["mis_pedidos"] = mis_pedidos[-30:]
    session.modified = True


    # Vaciar el carrito después de guardar el pedido
    session.pop("carrito", None)

    
    return redirect(
        url_for(
            "pedido_confirmado",
            id=str(resultado.inserted_id),
            token=token_pedido
        )
    )





# Confirmación privada del pedido
@app.route("/pedido/confirmado/<id>/<token>")
def pedido_confirmado(id, token):
    try:
        identificador = ObjectId(id)
    except (ValueError, TypeError):
        abort(404, description="Pedido no encontrado.")

    # Buscar el pedido y verificar su código privado
    pedido = pedidos.find_one({
        "_id": identificador,
        "token_confirmacion": token
    })

    if not pedido:
        abort(404, description="Pedido no encontrado.")

    # Preparar mensaje para WhatsApp
    lineas_productos = []

    for producto in pedido["productos"]:
        nombre = producto["nombre"]
        cantidad = producto["cantidad"]
        subtotal = producto["subtotal_centavos"] / 100

        lineas_productos.append(
            f"- {nombre} x{cantidad}: ${subtotal:.2f} MXN"
        )

    productos_texto = "\n".join(lineas_productos)
    total = pedido["total_centavos"] / 100

    mensaje_whatsapp = (
        "Hola, G&A Sweet Dreams! 🧁💗\n"
        "Quiero consultar mi pedido.\n\n"
        f"Pedido: {pedido['_id']}\n"
        f"Cliente: {pedido['nombre']}\n"
        f"Fecha solicitada: {pedido['fecha_entrega']}\n\n"
        f"Productos:\n{productos_texto}\n\n"
        f"Total: ${total:.2f} MXN\n\n"
        "Muchas gracias."
    )

    enlace_whatsapp = (
        f"https://wa.me/{WHATSAPP_NEGOCIO}"
        f"?text={quote(mensaje_whatsapp)}"
    )

    return render_template(
        "pedido_confirmado.html",
        pedido=pedido,
        enlace_whatsapp=enlace_whatsapp
    )



# Consultar los pedidos del cliente
@app.route("/mis-pedidos")
def mis_pedidos():
    referencias = session.get("mis_pedidos", [])
    lista_pedidos = []

    for referencia in referencias:
        try:
            identificador = ObjectId(referencia["id"])
            token = referencia["token"]
        except (KeyError, TypeError, ValueError):
            continue

        pedido = pedidos.find_one({
            "_id": identificador,
            "token_confirmacion": token
        })

        if pedido:
            lista_pedidos.append(pedido)

    # Mostrar primero los pedidos más recientes
    lista_pedidos.reverse()

    return render_template(
        "mis_pedidos.html",
        lista_pedidos=lista_pedidos
    )




# Panel de administración
@app.route("/admin")
@administrador_requerido
def administracion():
    lista = list(productos.find().sort("nombre", 1))
    return render_template(
        "index.html",
        productos=lista,
        categorias=CATEGORIAS,
        estados=ESTADOS
    )


# Panel administrativo de pedidos
@app.route("/admin/pedidos")
@administrador_requerido
def administrar_pedidos():

    # Consultar pedidos del más reciente al más antiguo
    lista_pedidos = list(
        pedidos.find().sort("fecha_registro", -1)
    )

    return render_template(
        "admin_pedidos.html",
        lista_pedidos=lista_pedidos
    )
    

# Actualizar el estado de un pedido
@app.route("/admin/pedidos/<id>/estado", methods=["POST"])
@administrador_requerido
def actualizar_estado_pedido(id):
    from datetime import datetime, timezone

    try:
        identificador = ObjectId(id)
    except (ValueError, TypeError):
        abort(404, description="Pedido no encontrado.")

    accion = request.form.get("accion", "").strip()
    mensaje = request.form.get("mensaje", "").strip()

    if accion not in ("aceptar", "rechazar", "entregar"):
        abort(400, description="Acción no válida.")

    if len(mensaje) > 500:
        abort(400, description="El mensaje es demasiado largo.")

    if accion == "rechazar" and not mensaje:
        abort(400, description="Debes explicar el rechazo.")

    # Definir los cambios permitidos
    if accion == "aceptar":
        estado_anterior = "Pendiente"
        nuevo_estado = "Aceptado"

    elif accion == "rechazar":
        estado_anterior = "Pendiente"
        nuevo_estado = "Rechazado"

    else:
        estado_anterior = "Aceptado"
        nuevo_estado = "Entregado"

    cambios = {
        "estado": nuevo_estado,
        "fecha_actualizacion": datetime.now(timezone.utc)
    }

    if accion in ("aceptar", "rechazar"):
        cambios["mensaje_admin"] = mensaje

    elif mensaje:
        cambios["mensaje_admin"] = mensaje

    # Actualizar solo si el pedido tiene el estado correcto
    resultado = pedidos.update_one(
        {
            "_id": identificador,
            "estado": estado_anterior
        },
        {"$set": cambios}
    )

    if resultado.matched_count == 0:
        abort(
            400,
            description="El pedido no existe o su estado ya cambió."
        )

    return redirect(url_for("administrar_pedidos"))





@app.route("/agregar", methods=["POST"])
@administrador_requerido
def agregar():
    datos = leer_formulario()

    # Obtener la fotografía seleccionada
    archivo = request.files.get("imagen")

    try:
        nombre_imagen = guardar_imagen(archivo)
    except ValueError as error:
        abort(400, description=str(error))

    # Guardar el nombre de la imagen en MongoDB
    datos["imagen"] = nombre_imagen

    try:
        productos.insert_one(datos)
    except Exception:
        # Evitar dejar una fotografía huérfana si falla MongoDB
        ruta = os.path.join(app.config["UPLOAD_FOLDER"], nombre_imagen)
        if os.path.isfile(ruta):
            os.remove(ruta)
        raise

    return redirect(url_for("administracion"))




# Editar producto
@app.route("/editar/<id>", methods=["GET", "POST"])
@administrador_requerido
def editar(id):
    producto = buscar_producto(id)

    if request.method == "POST":
        datos = leer_formulario()

        # Revisar si seleccionamos una nueva fotografía
        archivo = request.files.get("imagen")
        nombre_nuevo = None

        if archivo and archivo.filename:
            try:
                nombre_nuevo = guardar_imagen(archivo)
                datos["imagen"] = nombre_nuevo
            except ValueError as error:
                abort(400, description=str(error))

        try:
            productos.update_one(
                {"_id": producto["_id"]},
                {"$set": datos}
            )
        except Exception:
            # Si MongoDB falla, borrar la imagen nueva
            if nombre_nuevo:
                ruta = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    nombre_nuevo
                )
                if os.path.isfile(ruta):
                    os.remove(ruta)
            raise

        # Eliminar la fotografía anterior si fue reemplazada
        if nombre_nuevo:
            nombre_anterior = producto.get("imagen")

            if nombre_anterior and nombre_anterior != nombre_nuevo:
                ruta_anterior = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    nombre_anterior
                )

                if os.path.isfile(ruta_anterior):
                    os.remove(ruta_anterior)

        return redirect(url_for("administracion"))

    return render_template(
        "editar.html",
        producto=producto,
        categorias=CATEGORIAS,
        estados=ESTADOS
    )




# Eliminar producto
@app.route("/eliminar/<id>", methods=["POST"])
@administrador_requerido
def eliminar(id):
    producto = buscar_producto(id)

    # Eliminar primero el producto de MongoDB
    resultado = productos.delete_one(
        {"_id": producto["_id"]}
    )

    # Si se eliminó correctamente, borrar su fotografía
    if resultado.deleted_count == 1:
        nombre_imagen = producto.get("imagen")

        if nombre_imagen:
            # Solo permitir nombres de archivo, no rutas
            nombre_seguro = os.path.basename(nombre_imagen)

            if nombre_seguro == nombre_imagen:
                ruta = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    nombre_seguro
                )

                # Evitar borrar una imagen usada por otro producto
                otro_producto = productos.find_one(
                    {"imagen": nombre_seguro}
                )

                if not otro_producto and os.path.isfile(ruta):
                    os.remove(ruta)

    return redirect(url_for("administracion"))



# API JSON - Consultar productos
@app.route("/api/productos", methods=["GET"])
def api_listar():
    lista = productos.find().sort("nombre", 1)
    return jsonify([convertir_json(p) for p in lista])


# API JSON - Consultar uno
@app.route("/api/productos/<id>", methods=["GET"])
def api_consultar(id):
    producto = buscar_producto(id)
    return jsonify(convertir_json(producto))


# API JSON - Crear producto
@app.route("/api/productos", methods=["POST"])
@administrador_requerido
def api_crear():
    datos = request.get_json(silent=True)

    if not isinstance(datos, dict):
        return jsonify({"error": "Envía un objeto JSON válido."}), 400

    try:
        datos = validar_producto(datos)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    resultado = productos.insert_one(datos)
    datos["_id"] = resultado.inserted_id

    return jsonify(convertir_json(datos)), 201


# API JSON - Actualizar producto
@app.route("/api/productos/<id>", methods=["PUT"])
@administrador_requerido
def api_actualizar(id):
    producto = buscar_producto(id)
    datos = request.get_json(silent=True)

    if not isinstance(datos, dict):
        return jsonify({"error": "Envía un objeto JSON válido."}), 400

    try:
        datos = validar_producto(datos)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    productos.update_one(
        {"_id": producto["_id"]},
        {"$set": datos}
    )

    datos["_id"] = producto["_id"]
    return jsonify(convertir_json(datos))


# API JSON - Eliminar producto
@app.route("/api/productos/<id>", methods=["DELETE"])
@administrador_requerido
def api_eliminar(id):
    producto = buscar_producto(id)
    productos.delete_one({"_id": producto["_id"]})

    return jsonify({"mensaje": "Producto eliminado correctamente."})


# Página de ayuda
@app.route("/ayuda")
def ayuda():
    fecha = datetime.now().strftime("%d/%m/%Y")
    return render_template("ayuda.html", fecha=fecha)


# Errores de MongoDB
@app.errorhandler(PyMongoError)
def error_mongo(error):
    mensaje = "No fue posible conectar con MongoDB."
    if request.path.startswith("/api/"):
        return jsonify({"error": mensaje}), 503
    return render_template("error.html", mensaje=mensaje), 503


# Errores de peticiones
@app.errorhandler(400)
@app.errorhandler(404)
def error_peticion(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": error.description}), error.code

    return render_template(
        "error.html",
        mensaje=error.description
    ), error.code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

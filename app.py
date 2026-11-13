
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify, session
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
import hmac
import secrets
from functools import wraps
from werkzeug.security import check_password_hash

import io
import qrcode
from flask import send_file

from reservas_temporales import (
    reservar_producto,
    cancelar_reserva,
    liberar_reservas_vencidas,
    reducir_reserva
)

from confirmar_reservas import (
    confirmar_pedido_con_reservas,
    ReservaNoDisponible
)

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
MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb://127.0.0.1:27017/"
)

cliente = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=3000,
    tz_aware=True
)

# Permitir una base de datos diferente para las pruebas
NOMBRE_BASE_DATOS = os.environ.get(
    "MONGO_DB_NAME",
    "ga_sweet_dreams"
)

base_datos = cliente[NOMBRE_BASE_DATOS]

productos = base_datos["productos"]
pedidos = base_datos["pedidos"]
reservas = base_datos["reservas"]


# Identificar las reservas temporales de cada visitante
def obtener_id_reserva():
    reserva_id = session.get("reserva_id")

    if not isinstance(reserva_id, str) or not reserva_id:
        reserva_id = uuid.uuid4().hex
        session["reserva_id"] = reserva_id

    return reserva_id




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

    # Validar las existencias del producto
    existencias_texto = str(
        datos.get("existencias") if datos.get("existencias") is not None else ""
    ).strip()

    if not existencias_texto.isdecimal():
        raise ValueError(
            "Las existencias deben ser un número entero no negativo."
        )

    existencias = int(existencias_texto)

    if existencias > 100000:
        raise ValueError(
            "Las existencias no pueden superar 100000 unidades."
        )

    return {
        "nombre": nombre,
        "categoria": categoria,
        "estado": estado,
        "precio_centavos": precio_centavos,
        "existencias": existencias
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


# Ficha pública del producto para el código QR
@app.route("/ficha/<id>")
def ficha_producto(id):
    producto = buscar_producto(id)

    return render_template(
        "ficha_producto.html",
        producto=producto
    )
    

# Generar código QR para la ficha pública del producto
@app.route("/qr/producto/<id>")
def qr_producto(id):
    # Verificar que el producto exista en MongoDB
    producto = buscar_producto(id)

    # Construir la dirección pública de la ficha
    base_url = os.environ.get(
        "APP_BASE_URL",
        "https://ga-sweet-dreams-2.onrender.com"
    ).rstrip("/")

    url_ficha = f"{base_url}{url_for('ficha_producto', id=id)}"

    # Generar el código QR
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    qr.add_data(url_ficha)
    qr.make(fit=True)

    imagen = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    # Guardar la imagen en memoria
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/png"
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


    # Comprobar que la cantidad total no supere 99
    cantidad_actual = carrito.get(id, 0)

    if not isinstance(cantidad_actual, int):
        abort(400, description="Cantidad del carrito no válida.")

    if cantidad_actual + cantidad > 99:
        abort(400, description="No puedes agregar más de 99 unidades.")

    # Liberar reservas vencidas antes de consultar disponibilidad
    liberar_reservas_vencidas(cliente, productos, reservas)

    # Identificar al visitante
    reserva_id = obtener_id_reserva()


    # Reservar todas las unidades en una sola transacción
    try:
        aceptada = reservar_producto(
            cliente,
            productos,
            reservas,
            id,
            reserva_id,
            cantidad=cantidad
        )
    except ValueError as error:
        abort(400, description=str(error))

    if not aceptada:
        abort(
            400,
            description="No hay suficientes unidades disponibles."
        )

    # Actualizar el carrito solo después de reservar
    carrito[id] = cantidad_actual + cantidad



    # Guardar los cambios
    session["carrito"] = carrito

    return redirect(url_for("catalogo"))



@app.route("/carrito/actualizar/<id>", methods=["POST"])
def actualizar_carrito(id):
    carrito = session.get("carrito", {})

    if id not in carrito:
        abort(404, description="Producto no encontrado en el carrito.")

    accion = request.form.get("accion")

    if accion not in ("aumentar", "disminuir", "eliminar"):
        abort(400, description="Acción no válida.")

    try:
        cantidad = int(carrito[id])
    except (ValueError, TypeError):
        abort(400, description="Cantidad no válida.")

    if not 1 <= cantidad <= 99:
        abort(400, description="Cantidad fuera del rango permitido.")

    # Liberar reservas vencidas
    liberar_reservas_vencidas(cliente, productos, reservas)

    reserva_id = session.get("reserva_id")

    if not reserva_id:
        abort(400, description="La reserva no existe o venció.")

    # Verificar que la reserva siga vigente
    from datetime import datetime, timezone

    reserva_actual = reservas.find_one({
        "reserva_id": reserva_id,
        "producto_id": ObjectId(id),
        "estado": "activa",
        "vence_en": {"$gt": datetime.now(timezone.utc)}
    })

    if reserva_actual is None:
        session.pop("carrito", None)
        abort(400, description="Tu reserva venció. Agrega nuevamente los productos.")

    if reserva_actual.get("cantidad") != cantidad:
        abort(400, description="La cantidad reservada no coincide con el carrito.")

    if accion == "aumentar":
        if cantidad >= 99:
            abort(400, description="Máximo 99 unidades.")

        aceptada = reservar_producto(
            cliente, productos, reservas, id, reserva_id, cantidad=1
        )

        if not aceptada:
            abort(400, description="No hay más unidades disponibles.")

        carrito[id] = cantidad + 1

    elif accion == "disminuir":
        liberada = reducir_reserva(
            cliente, productos, reservas, id, reserva_id, cantidad=1
        )

        if not liberada:
            abort(400, description="No se pudo reducir la reserva.")

        if cantidad == 1:
            carrito.pop(id, None)
        else:
            carrito[id] = cantidad - 1

    elif accion == "eliminar":
        cancelada = cancelar_reserva(
            cliente, productos, reservas, id, reserva_id
        )

        if not cancelada:
            abort(400, description="No se pudo cancelar la reserva.")

        carrito.pop(id, None)

    session["carrito"] = carrito

    return redirect(url_for("ver_carrito"))






@app.route("/carrito/vaciar", methods=["POST"])
def vaciar_carrito():
    carrito = session.get("carrito", {})
    reserva_id = session.get("reserva_id")

    if reserva_id:
        # Liberar las reservas de este visitante
        reservas_cliente = list(reservas.find({
            "reserva_id": reserva_id,
            "estado": "activa"
        }))

        for reserva in reservas_cliente:
            cancelar_reserva(
                cliente,
                productos,
                reservas,
                str(reserva["producto_id"]),
                reserva_id
            )

    # Vaciar la sesión solo después de liberar las reservas
    session.pop("carrito", None)

    return redirect(url_for("ver_carrito"))



# Mostrar el carrito de compras
@app.route("/carrito")
def ver_carrito():
    carrito = session.get("carrito", {})


    # Liberar las reservas vencidas antes de mostrar el carrito
    liberar_reservas_vencidas(cliente, productos, reservas)

    reserva_id = session.get("reserva_id")
    carrito_actualizado = {}
    vencimientos = {}

    if reserva_id and isinstance(carrito, dict):
        ahora = datetime.now(timezone.utc)

        for id_producto, cantidad in carrito.items():
            if not ObjectId.is_valid(id_producto):
                continue

            reserva = reservas.find_one({
                "reserva_id": reserva_id,
                "producto_id": ObjectId(id_producto),
                "estado": "activa",
                "vence_en": {"$gt": ahora}
            })

            if (
                reserva
                and type(cantidad) is int
                and 1 <= cantidad <= 99
                and reserva.get("cantidad") == cantidad
            ):
                # Guardar la fecha de vencimiento de cada reserva
                if "vencimientos" not in locals():
                    vencimientos = {}
                    vencimientos[id_producto] = reserva["vence_en"]

                carrito_actualizado[id_producto] = cantidad
                vencimientos[id_producto] = reserva["vence_en"]

    # Sincronizar el carrito con las reservas vigentes
    carrito = carrito_actualizado
    session["carrito"] = carrito



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
    total_centavos=total_centavos,
    vencimientos={
        id_producto: fecha.isoformat()
        for id_producto, fecha in vencimientos.items()
    }
)



@app.route("/checkout", methods=["GET"])
def checkout():
    # Liberar las reservas que ya vencieron
    liberar_reservas_vencidas(cliente, productos, reservas)

    carrito = session.get("carrito", {})
    reserva_id = session.get("reserva_id")

    if not isinstance(carrito, dict) or not carrito or not reserva_id:
        return redirect(url_for("ver_carrito"))

    ahora = datetime.now(timezone.utc)
    vencimientos = {}

    for id_producto, cantidad in carrito.items():
        if not ObjectId.is_valid(id_producto):
            return redirect(url_for("ver_carrito"))

        reserva = reservas.find_one({
            "reserva_id": reserva_id,
            "producto_id": ObjectId(id_producto),
            "estado": "activa",
            "vence_en": {"$gt": ahora}
        })

        if (
            reserva is None
            or type(cantidad) is not int
            or reserva.get("cantidad") != cantidad
        ):
            return redirect(url_for("ver_carrito"))

        vencimientos[id_producto] = reserva["vence_en"]

    return render_template(
        "checkout.html",
        vencimientos={
            id_producto: fecha.isoformat()
            for id_producto, fecha in vencimientos.items()
            }
        )



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


    # Confirmar el pedido y descontar las existencias
    # mediante una transacción de MongoDB
    liberar_reservas_vencidas(cliente, productos, reservas)

    reserva_id = session.get("reserva_id")

    try:
        pedido_id = confirmar_pedido_con_reservas(
            cliente,
            productos,
            reservas,
            pedidos,
            reserva_id,
            carrito,
            nuevo_pedido
        )
    except ReservaNoDisponible as error:
        abort(400, description=str(error))


    # Guardar una referencia privada al pedido en la sesión
    mis_pedidos = session.get("mis_pedidos", [])

    mis_pedidos.append({
        "id": str(pedido_id),
        "token": token_pedido
        })

    session["mis_pedidos"] = mis_pedidos[-30:]
    session.modified = True


    # Vaciar el carrito después de guardar el pedido
    session.pop("carrito", None)


    return redirect(
        url_for(
            "pedido_confirmado",
            id=str(pedido_id),
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

    # Todo producto nuevo comienza sin reservas
    datos["reservadas"] = 0

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
        ruta = os.path.join(
            app.config["UPLOAD_FOLDER"],
            nombre_imagen
        )

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
            # Actualizar solo si las nuevas existencias
            # cubren todas las unidades reservadas.
            resultado = productos.update_one(
                {
                    "_id": producto["_id"],
                    "reservadas": {
                        "$lte": datos["existencias"]
                    }
                },
                {
                    "$set": datos
                }
            )

            if resultado.matched_count == 0:
                abort(
                    409,
                    description=(
                        "No puedes reducir las existencias "
                        "por debajo de las unidades reservadas."
                    )
                )

        except Exception:
            # Si falla la actualización, borrar imagen nueva
            if nombre_nuevo:
                ruta = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    nombre_nuevo
                )

                if os.path.isfile(ruta):
                    os.remove(ruta)

            raise

        # Eliminar fotografía anterior si fue reemplazada
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


    # Eliminar únicamente si no existen unidades reservadas
    resultado = productos.delete_one(
        {
            "_id": producto["_id"],
            "reservadas": 0
        }
    )

    if resultado.deleted_count != 1:
        abort(
            409,
            description=(
                "No se puede eliminar este producto porque "
                "tiene unidades reservadas o su inventario "
                "necesita revisión."
            )
        )


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
@app.errorhandler(409)
def error_peticion(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "error": error.description
        }), error.code

    return render_template(
        "error.html",
        mensaje=error.description
    ), error.code



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

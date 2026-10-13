
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from datetime import datetime
import os

app = Flask(__name__)

# Base de datos independiente
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/")
cliente = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
base_datos = cliente["ga_sweet_dreams"]
productos = base_datos["productos"]

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
        "estado": estado
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


# Página principal
@app.route("/")
def inicio():
    lista = list(productos.find().sort("nombre", 1))
    return render_template(
        "index.html",
        productos=lista,
        categorias=CATEGORIAS,
        estados=ESTADOS
    )


# Registrar producto
@app.route("/agregar", methods=["POST"])
def agregar():
    datos = leer_formulario()
    productos.insert_one(datos)
    return redirect(url_for("inicio"))


# Editar producto
@app.route("/editar/<id>", methods=["GET", "POST"])
def editar(id):
    producto = buscar_producto(id)

    if request.method == "POST":
        datos = leer_formulario()
        productos.update_one(
            {"_id": producto["_id"]},
            {"$set": datos}
        )
        return redirect(url_for("inicio"))

    return render_template(
        "editar.html",
        producto=producto,
        categorias=CATEGORIAS,
        estados=ESTADOS
    )


# Eliminar producto
@app.route("/eliminar/<id>", methods=["POST"])
def eliminar(id):
    producto = buscar_producto(id)
    productos.delete_one({"_id": producto["_id"]})
    return redirect(url_for("inicio"))


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

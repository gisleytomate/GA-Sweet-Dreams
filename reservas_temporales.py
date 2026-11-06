
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

DURACION_RESERVA_MINUTOS = 5



def reservar_producto(
    cliente, productos, reservas, producto_id, reserva_id, cantidad=1
):
    if not ObjectId.is_valid(producto_id):
        raise ValueError("El producto no es válido.")

    if not reserva_id:
        raise ValueError("La reserva no tiene identificador.")

    if type(cantidad) is not int or not 1 <= cantidad <= 99:
        raise ValueError("Cantidad no válida.")

    identificador = ObjectId(producto_id)
    ahora = datetime.now(timezone.utc)
    vencimiento = ahora + timedelta(
        minutes=DURACION_RESERVA_MINUTOS
    )

    def operacion(sesion):
        reserva_anterior = reservas.find_one(
            {
                "reserva_id": reserva_id,
                "producto_id": identificador
            },
            session=sesion
        )

        if reserva_anterior:
            if (
                reserva_anterior.get("estado") != "activa"
                or reserva_anterior["vence_en"] <= ahora
            ):
                raise ValueError(
                    "La reserva anterior venció. Actualiza tu carrito."
                )

        producto = productos.find_one_and_update(
            {
                "_id": identificador,
                "estado": "Disponible",
                "$expr": {
                    "$gte": [
                        {
                            "$subtract": [
                                {"$ifNull": ["$existencias", 0]},
                                {"$ifNull": ["$reservadas", 0]}
                            ]
                        },
                        cantidad
                    ]
                }
            },
            {"$inc": {"reservadas": cantidad}},
            return_document=ReturnDocument.AFTER,
            session=sesion
        )

        if producto is None:
            return False

        reservas.update_one(
            {
                "reserva_id": reserva_id,
                "producto_id": identificador
            },
            {
                "$inc": {"cantidad": cantidad},
                "$set": {
                    "estado": "activa",
                    "vence_en": vencimiento
                },
                "$setOnInsert": {
                    "creada_en": ahora
                }
            },
            upsert=True,
            session=sesion
        )

        return True

    with cliente.start_session() as sesion:
        return sesion.with_transaction(operacion)



def cancelar_reserva(cliente, productos, reservas, producto_id, reserva_id):
    if not ObjectId.is_valid(producto_id):
        raise ValueError("El producto no es válido.")

    if not reserva_id:
        raise ValueError("La reserva no tiene identificador.")

    identificador = ObjectId(producto_id)

    def operacion(sesion):
        reserva = reservas.find_one_and_delete(
            {
                "reserva_id": reserva_id,
                "producto_id": identificador,
                "estado": "activa"
            },
            session=sesion
        )

        if reserva is None:
            return False

        resultado = productos.update_one(
            {
                "_id": identificador,
                "reservadas": {"$gte": reserva["cantidad"]}
            },
            {
                "$inc": {
                    "reservadas": -reserva["cantidad"]
                }
            },
            session=sesion
        )

        if resultado.modified_count != 1:
            raise RuntimeError(
                "No fue posible liberar las unidades reservadas."
            )

        return True

    with cliente.start_session() as sesion:
        return sesion.with_transaction(operacion)



def liberar_reservas_vencidas(cliente, productos, reservas):
    ahora = datetime.now(timezone.utc)

    vencidas = list(reservas.find({
        "estado": "activa",
        "vence_en": {"$lte": ahora}
    }))

    liberadas = 0

    for reserva in vencidas:

        def operacion(sesion):
            eliminada = reservas.find_one_and_delete(
                {
                    "_id": reserva["_id"],
                    "estado": "activa",
                    "vence_en": {"$lte": datetime.now(timezone.utc)}
                },
                session=sesion
            )

            if eliminada is None:
                return False

            resultado = productos.update_one(
                {
                    "_id": eliminada["producto_id"],
                    "reservadas": {"$gte": eliminada["cantidad"]}
                },
                {
                    "$inc": {
                        "reservadas": -eliminada["cantidad"]
                    }
                },
                session=sesion
            )

            if resultado.modified_count != 1:
                raise RuntimeError(
                    "No se pudo liberar el inventario de una reserva vencida."
                )

            return True

        with cliente.start_session() as sesion:
            if sesion.with_transaction(operacion):
                liberadas += 1

    return liberadas



def reducir_reserva(
    cliente, productos, reservas, producto_id, reserva_id, cantidad=1
):
    if not ObjectId.is_valid(producto_id):
        raise ValueError("El producto no es válido.")

    if not reserva_id:
        raise ValueError("La reserva no tiene identificador.")

    if type(cantidad) is not int or not 1 <= cantidad <= 99:
        raise ValueError("Cantidad no válida.")

    identificador = ObjectId(producto_id)

    def operacion(sesion):
        reserva = reservas.find_one(
            {
                "reserva_id": reserva_id,
                "producto_id": identificador,
                "estado": "activa"
            },
            session=sesion
        )

        if reserva is None:
            return False

        if reserva["cantidad"] < cantidad:
            raise ValueError(
                "No puedes liberar más unidades de las reservadas."
            )

        if reserva["cantidad"] == cantidad:
            reservas.delete_one(
                {"_id": reserva["_id"]},
                session=sesion
            )
        else:
            reservas.update_one(
                {"_id": reserva["_id"]},
                {"$inc": {"cantidad": -cantidad}},
                session=sesion
            )

        resultado = productos.update_one(
            {
                "_id": identificador,
                "reservadas": {"$gte": cantidad}
            },
            {"$inc": {"reservadas": -cantidad}},
            session=sesion
        )

        if resultado.modified_count != 1:
            raise RuntimeError(
                "No fue posible liberar las unidades reservadas."
            )

        return True

    with cliente.start_session() as sesion:
        return sesion.with_transaction(operacion)

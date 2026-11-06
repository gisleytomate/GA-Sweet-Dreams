
from datetime import datetime, timezone
from bson import ObjectId


class ReservaNoDisponible(Exception):
    pass


def confirmar_pedido_con_reservas(
    cliente, productos, reservas, pedidos,
    reserva_id, carrito, nuevo_pedido
):
    if not isinstance(reserva_id, str) or not reserva_id:
        raise ReservaNoDisponible("No existe una reserva válida.")

    if not isinstance(carrito, dict) or not carrito:
        raise ReservaNoDisponible("El carrito está vacío.")

    def operacion(sesion):
        ahora = datetime.now(timezone.utc)

        # Validar todos los productos antes de confirmar
        for id_producto, cantidad in carrito.items():
            if (
                not ObjectId.is_valid(id_producto)
                or type(cantidad) is not int
                or not 1 <= cantidad <= 99
            ):
                raise ReservaNoDisponible(
                    "El carrito contiene datos no válidos."
                )

            identificador = ObjectId(id_producto)

            reserva = reservas.find_one(
                {
                    "reserva_id": reserva_id,
                    "producto_id": identificador,
                    "estado": "activa",
                    "vence_en": {"$gt": ahora},
                    "cantidad": cantidad
                },
                session=sesion
            )

            if reserva is None:
                raise ReservaNoDisponible(
                    "Una reserva venció o cambió de cantidad."
                )

            producto = productos.find_one(
                {
                    "_id": identificador,
                    "estado": "Disponible",
                    "existencias": {"$gte": cantidad},
                    "reservadas": {"$gte": cantidad}
                },
                session=sesion
            )

            if producto is None:
                raise ReservaNoDisponible(
                    "Un producto ya no tiene existencias suficientes."
                )


            # Verificar que el precio y los datos del pedido
            # coincidan con los del producto en MongoDB
            detalles = nuevo_pedido.get("productos", [])

            coincidencias = [
                item for item in detalles
                if item.get("producto_id") == id_producto
            ]

            if len(coincidencias) != 1:
                raise ReservaNoDisponible(
                    "Los productos del pedido no coinciden con el carrito."
                )

            detalle = coincidencias[0]
            precio_actual = producto.get("precio_centavos")

            if (
                type(precio_actual) is not int
                or precio_actual <= 0
                or detalle.get("precio_centavos") != precio_actual
                or detalle.get("cantidad") != cantidad
                or detalle.get("subtotal_centavos") != precio_actual * cantidad
                or detalle.get("nombre") != producto.get("nombre")
            ):
                raise ReservaNoDisponible(
                    "Los datos o precios del pedido cambiaron. "
                    "Actualiza el carrito e inténtalo nuevamente."
                )


        # Descontar inventario y reservas
        for id_producto, cantidad in carrito.items():
            identificador = ObjectId(id_producto)

            resultado = productos.update_one(
                {
                    "_id": identificador,
                    "estado": "Disponible",
                    "existencias": {"$gte": cantidad},
                    "reservadas": {"$gte": cantidad}
                },
                {
                    "$inc": {
                        "existencias": -cantidad,
                        "reservadas": -cantidad
                    }
                },
                session=sesion
            )

            if resultado.modified_count != 1:
                raise ReservaNoDisponible(
                    "No fue posible actualizar el inventario."
                )

            eliminada = reservas.delete_one(
                {
                    "reserva_id": reserva_id,
                    "producto_id": identificador,
                    "estado": "activa",
                    "vence_en": {"$gt": ahora},
                    "cantidad": cantidad
                },
                session=sesion
            )

            if eliminada.deleted_count != 1:
                raise ReservaNoDisponible(
                    "La reserva ya no está disponible."
                )

        # Verificar el total del pedido antes de guardarlo
        detalles = nuevo_pedido.get("productos", [])

        if not isinstance(detalles, list):
            raise ReservaNoDisponible(
                "El detalle del pedido no es válido."
            )

        total_calculado = sum(
            item["subtotal_centavos"]
            for item in detalles
        )

        if (
            len(detalles) != len(carrito)
            or nuevo_pedido.get("total_centavos") != total_calculado
        ):
            raise ReservaNoDisponible(
                "El total del pedido no coincide con los productos."
            )


        # Guardar el pedido en la misma transacción
        resultado = pedidos.insert_one(
            nuevo_pedido,
            session=sesion
        )

        return resultado.inserted_id

    with cliente.start_session() as sesion:
        return sesion.with_transaction(operacion)

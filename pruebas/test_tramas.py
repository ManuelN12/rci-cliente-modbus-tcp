"""
Pruebas sin simulador: cada trama TX/RX de ejemplo del guion de la práctica.

Para cada ejemplo, un servidor falso (hilo local) comprueba que la trama
enviada por el cliente es exactamente la TX del guion y contesta con la RX.
Así se prueban a la vez pdu(), la cabecera MBAP, la lectura por TCP y la
interpretación de la respuesta.

Ejecutar desde la raíz del repositorio:
    python3 -m unittest discover -s pruebas -v
"""

import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "practica1"))

import modbus_tcp as mb  # noqa: E402


class ServidorFalso:
    """Servidor TCP de una conexión que responde con tramas fijas.

    `pares` es una lista de (tx_esperada, rx_a_enviar). Si rx es None no
    responde (para provocar un timeout). `partir` envía la respuesta en dos
    trozos para comprobar que el cliente junta los recv().
    """

    def __init__(self, pares, partir=False):
        self.pares = [(bytes.fromhex(tx), rx and bytes.fromhex(rx)) for tx, rx in pares]
        self.partir = partir
        self.recibidas = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.puerto = self.sock.getsockname()[1]
        self.hilo = threading.Thread(target=self._atender, daemon=True)
        self.hilo.start()

    def _atender(self):
        conn, _ = self.sock.accept()
        with conn:
            for tx, rx in self.pares:
                datos = conn.recv(300)
                self.recibidas.append(datos)
                if rx is None:
                    continue
                if self.partir:
                    conn.sendall(rx[:3])
                    threading.Event().wait(0.05)
                    conn.sendall(rx[3:])
                else:
                    conn.sendall(rx)
            threading.Event().wait(0.5)
        self.sock.close()


def cliente_con_tid(puerto, tid, **kw):
    """Cliente cuyo próximo Transaction ID será `tid` (como en el guion)."""
    c = mb.ClienteModbusTCP("127.0.0.1", puerto, unidad=1, timeout=1, **kw)
    c._transaccion = tid - 1
    return c


class TestTramasDelGuion(unittest.TestCase):

    def ejecutar(self, tid, tx, rx, peticion, partir=False):
        srv = ServidorFalso([(tx, rx)], partir=partir)
        with cliente_con_tid(srv.puerto, tid) as c:
            resultado = c.ejecutar(peticion)
        self.assertEqual(srv.recibidas[0], bytes.fromhex(tx))
        return resultado

    def test_01_leer_coils(self):
        r = self.ejecutar(1, "00 01 00 00 00 06 01 01 00 14 00 0A",
                          "00 01 00 00 00 05 01 01 02 4D 03", mb.LeerCoils(20, 10))
        self.assertEqual(r, [1, 0, 1, 1, 0, 0, 1, 0, 1, 1])

    def test_02_leer_entradas(self):
        r = self.ejecutar(2, "00 02 00 00 00 06 01 02 00 00 00 05",
                          "00 02 00 00 00 04 01 02 01 13", mb.LeerEntradasDiscretas(0, 5))
        self.assertEqual(r, [1, 1, 0, 0, 1])

    def test_03_leer_holding(self):
        r = self.ejecutar(3, "00 03 00 00 00 06 01 03 00 6B 00 03",
                          "00 03 00 00 00 09 01 03 06 02 2B 00 00 00 64",
                          mb.LeerHoldingRegisters(107, 3))
        self.assertEqual(r, [555, 0, 100])

    def test_03_respuesta_partida(self):
        r = self.ejecutar(3, "00 03 00 00 00 06 01 03 00 6B 00 03",
                          "00 03 00 00 00 09 01 03 06 02 2B 00 00 00 64",
                          mb.LeerHoldingRegisters(107, 3), partir=True)
        self.assertEqual(r, [555, 0, 100])

    def test_04_leer_input(self):
        r = self.ejecutar(4, "00 04 00 00 00 06 01 04 00 08 00 01",
                          "00 04 00 00 00 05 01 04 02 00 0A", mb.LeerInputRegisters(8, 1))
        self.assertEqual(r, [10])

    def test_05_escribir_coil(self):
        self.ejecutar(5, "00 05 00 00 00 06 01 05 00 AC FF 00",
                      "00 05 00 00 00 06 01 05 00 AC FF 00", mb.EscribirCoil(172, True))

    def test_06_escribir_registro(self):
        self.ejecutar(6, "00 06 00 00 00 06 01 06 00 01 00 03",
                      "00 06 00 00 00 06 01 06 00 01 00 03", mb.EscribirRegistro(1, 3))

    def test_06_negativo_en_complemento_a_2(self):
        self.assertEqual(mb.EscribirRegistro(1, -5).pdu(), bytes.fromhex("06 00 01 FF FB"))

    def test_0F_escribir_coils(self):
        self.ejecutar(7, "00 07 00 00 00 09 01 0F 00 13 00 0A 02 CD 01",
                      "00 07 00 00 00 06 01 0F 00 13 00 0A",
                      mb.EscribirCoils(19, [1, 0, 1, 1, 0, 0, 1, 1, 1, 0]))

    def test_10_escribir_registros(self):
        self.ejecutar(8, "00 08 00 00 00 0B 01 10 00 01 00 02 04 00 0A 01 02",
                      "00 08 00 00 00 06 01 10 00 01 00 02",
                      mb.EscribirRegistros(1, [0x000A, 0x0102]))

    def test_17_leer_escribir(self):
        r = self.ejecutar(
            10, "00 0A 00 00 00 11 01 17 00 03 00 06 00 0E 00 03 06 00 FF 00 FF 00 FF",
            "00 0A 00 00 00 0F 01 17 0C 00 FE 0A CD 00 01 00 03 00 0D 00 FF",
            mb.LeerEscribirRegistros(3, 6, 14, [0xFF, 0xFF, 0xFF]))
        self.assertEqual(r, [0x00FE, 0x0ACD, 1, 3, 0x000D, 0x00FF])

    def test_excepcion(self):
        with self.assertRaises(mb.ExcepcionModbus) as ctx:
            self.ejecutar(11, "00 0B 00 00 00 06 01 03 27 10 00 01",
                          "00 0B 00 00 00 03 01 83 02", mb.LeerHoldingRegisters(10000, 1))
        self.assertEqual(ctx.exception.codigo, 0x02)
        self.assertEqual(ctx.exception.nombre, "ILLEGAL DATA ADDRESS")


class TestErrores(unittest.TestCase):

    def test_timeout(self):
        srv = ServidorFalso([("00 01 00 00 00 06 01 03 00 00 00 01", None)])
        c = mb.ClienteModbusTCP("127.0.0.1", srv.puerto, unidad=1, timeout=0.3)
        with self.assertRaises(mb.ErrorTimeout):
            c.leer_holding_registers(0, 1)
        self.assertFalse(c.conectado)

    def test_conexion_rechazada(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
        s.close()   # nadie escucha en ese puerto
        with self.assertRaises(mb.ErrorConexion):
            mb.ClienteModbusTCP("127.0.0.1", puerto, timeout=0.5).conectar()

    def test_eco_incorrecto_es_error_de_protocolo(self):
        srv = ServidorFalso([("00 01 00 00 00 06 01 06 00 01 00 03",
                              "00 01 00 00 00 06 01 06 00 01 00 04")])
        with self.assertRaises(mb.ErrorProtocolo):
            cliente_con_tid(srv.puerto, 1).escribir_registro(1, 3)

    def test_numero_de_bytes_incorrecto(self):
        srv = ServidorFalso([("00 01 00 00 00 06 01 03 00 00 00 02",
                              "00 01 00 00 00 05 01 03 02 00 01")])
        with self.assertRaises(mb.ErrorProtocolo):
            cliente_con_tid(srv.puerto, 1).leer_holding_registers(0, 2)

    def test_argumentos_fuera_de_rango(self):
        for crear in (lambda: mb.LeerHoldingRegisters(0, 0),
                      lambda: mb.LeerHoldingRegisters(0, 126),
                      lambda: mb.LeerCoils(70000, 1),
                      lambda: mb.EscribirRegistro(0, 70000),
                      lambda: mb.EscribirRegistro(0, -40000),
                      lambda: mb.EscribirCoil(0, 2),
                      lambda: mb.EscribirRegistros(0, [1] * 124),
                      lambda: mb.EscribirCoils(0, [])):
            with self.assertRaises(ValueError):
                crear()


class TestCabecera(unittest.TestCase):

    def test_ida_y_vuelta(self):
        c = mb.CabeceraMBAP(1, 6, 1)
        self.assertEqual(c.a_bytes(), bytes.fromhex("00 01 00 00 00 06 01"))
        d = mb.CabeceraMBAP.desde_bytes(c.a_bytes())
        self.assertEqual((d.transaccion, d.protocolo, d.longitud, d.unidad), (1, 0, 6, 1))

    def test_transaccion_da_la_vuelta(self):
        c = mb.ClienteModbusTCP()
        c._transaccion = 0xFFFF
        self.assertEqual(c._siguiente_transaccion(), 1)


if __name__ == "__main__":
    unittest.main()

"""
modbus_tcp.py - Cliente Modbus TCP escrito a mano, byte a byte.

Práctica 1 de Redes de Comunicaciones Industriales (Universidad de Deusto).
Solo usa la biblioteca estándar de Python (socket y struct).

Estructura de la biblioteca
---------------------------
* CabeceraMBAP          Los 7 bytes que preceden a la PDU en Modbus TCP.
* Peticion              Clase base de todas las funciones. Cada subclase sabe
                        construir su PDU (pdu()) e interpretar la respuesta
                        (procesar_respuesta()).
    _LecturaBits        01 LeerCoils, 02 LeerEntradasDiscretas
    _LecturaRegistros   03 LeerHoldingRegisters, 04 LeerInputRegisters
    _EscrituraSimple    05 EscribirCoil, 06 EscribirRegistro (respuesta = eco)
    _EscrituraMultiple  0F EscribirCoils, 10 EscribirRegistros
    LeerEscribirRegistros  17
* ClienteModbusTCP      Abre la conexión, añade la MBAP, envía la trama,
                        lee la respuesta completa y la valida. Incluye atajos
                        (leer_holding_registers, escribir_coil, ...).
* Errores               ErrorModbus (base) -> ExcepcionModbus, ErrorTimeout,
                        ErrorConexion, ErrorProtocolo.

Ejemplo desde la consola de Python::

    >>> from modbus_tcp import ClienteModbusTCP
    >>> c = ClienteModbusTCP("127.0.0.1", 5020, traza=True)
    >>> c.leer_holding_registers(107, 3)
    TX: 00 01 00 00 00 06 01 03 00 6B 00 03
    RX: 00 01 00 00 00 09 01 03 06 02 2B 00 00 00 64
    [555, 0, 100]

Todos los campos de más de un byte viajan en big-endian (formato ">" de struct).
"""

import socket
import struct
import sys

AUTOR = "Manuel Narváez"

PUERTO_MODBUS = 502          # Puerto estándar (en Linux sin root el simulador usa 5020)
UNIDAD_POR_DEFECTO = 1       # Unit ID; un servidor TCP directo suele ignorarlo
TIMEOUT_POR_DEFECTO = 3.0    # Segundos

# Códigos de función
FC_LEER_COILS = 0x01
FC_LEER_ENTRADAS_DISCRETAS = 0x02
FC_LEER_HOLDING_REGISTERS = 0x03
FC_LEER_INPUT_REGISTERS = 0x04
FC_ESCRIBIR_COIL = 0x05
FC_ESCRIBIR_REGISTRO = 0x06
FC_ESCRIBIR_COILS = 0x0F
FC_ESCRIBIR_REGISTROS = 0x10
FC_LEER_ESCRIBIR_REGISTROS = 0x17

# Valores que viajan en la función 05: ON no es 1, es FF 00
COIL_ON = 0xFF00
COIL_OFF = 0x0000

NOMBRES_EXCEPCION = {
    0x01: "ILLEGAL FUNCTION",
    0x02: "ILLEGAL DATA ADDRESS",
    0x03: "ILLEGAL DATA VALUE",
    0x04: "SERVER DEVICE FAILURE",
    0x05: "ACKNOWLEDGE",
    0x06: "SERVER DEVICE BUSY",
    0x08: "MEMORY PARITY ERROR",
    0x0A: "GATEWAY PATH UNAVAILABLE",
    0x0B: "GATEWAY TARGET DEVICE FAILED TO RESPOND",
}


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

class ErrorModbus(Exception):
    """Clase base de todos los errores de la biblioteca."""


class ExcepcionModbus(ErrorModbus):
    """El servidor respondió con una excepción (función + 0x80, código).

    La red, TCP y la cabecera funcionan: el problema está en la PDU.
    """

    def __init__(self, funcion, codigo):
        self.funcion = funcion
        self.codigo = codigo
        self.nombre = NOMBRES_EXCEPCION.get(codigo, "DESCONOCIDA")
        super().__init__(
            f"Excepción 0x{codigo:02X} ({self.nombre}) en la función 0x{funcion:02X}"
        )


class ErrorTimeout(ErrorModbus):
    """No llegó una respuesta completa dentro del tiempo límite."""


class ErrorConexion(ErrorModbus):
    """No se pudo conectar o la conexión se cerró a mitad de la transacción."""


class ErrorProtocolo(ErrorModbus):
    """La respuesta llegó pero no cumple el protocolo (cabecera, longitud, eco...)."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def a_hex(datos):
    """Bytes -> '00 01 0A' (formato de la traza)."""
    return " ".join(f"{b:02X}" for b in datos)


def a_con_signo(valor):
    """Interpreta un registro de 16 bits como entero con signo (complemento a 2)."""
    return valor - 0x10000 if valor & 0x8000 else valor


def a_registro(valor):
    """Convierte -32768..65535 al valor de 16 bits que viaja en la trama.

    Los negativos se envían en complemento a 2: -5 -> 0xFFFB.
    """
    _comprobar_entero(valor, -32768, 65535, "valor de registro")
    return valor & 0xFFFF


def empaquetar_bits(valores):
    """Lista de bits -> bytes. El primer bit va en el bit 0 del primer byte.

    Los bits sobrantes del último byte quedan a 0.
    """
    resultado = bytearray((len(valores) + 7) // 8)
    for i, v in enumerate(valores):
        if v:
            resultado[i // 8] |= 1 << (i % 8)
    return bytes(resultado)


def desempaquetar_bits(datos, cantidad):
    """Bytes -> lista de `cantidad` booleanos (inverso de empaquetar_bits)."""
    return [bool(datos[i // 8] >> (i % 8) & 1) for i in range(cantidad)]


def _comprobar_entero(valor, minimo, maximo, nombre):
    """Lanza ValueError si `valor` no es un entero en [minimo, maximo]."""
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError(f"{nombre} debe ser un entero (recibido {valor!r})")
    if not minimo <= valor <= maximo:
        raise ValueError(f"{nombre} fuera de rango ({minimo}-{maximo}): {valor}")


def _a_bit(valor):
    """Acepta True/False o 1/0 como valor de una coil."""
    if isinstance(valor, bool):
        return valor
    if valor in (0, 1):
        return bool(valor)
    raise ValueError(f"valor de coil inválido (usar 0/1 o True/False): {valor!r}")


# ---------------------------------------------------------------------------
# Cabecera MBAP
# ---------------------------------------------------------------------------

class CabeceraMBAP:
    """Cabecera MBAP de 7 bytes: Transaction ID, Protocol ID, Length, Unit ID.

    Length cuenta los bytes que siguen: 1 (Unit ID) + longitud de la PDU.
    """

    TAMANO = 7
    FORMATO = ">HHHB"
    LONGITUD_MAXIMA = 254   # 1 (Unit ID) + 253 (PDU máxima)

    def __init__(self, transaccion, longitud, unidad, protocolo=0):
        self.transaccion = transaccion
        self.protocolo = protocolo
        self.longitud = longitud
        self.unidad = unidad

    @classmethod
    def para_pdu(cls, transaccion, unidad, pdu):
        """Construye la cabecera que corresponde a una PDU concreta."""
        return cls(transaccion, 1 + len(pdu), unidad)

    def a_bytes(self):
        return struct.pack(self.FORMATO, self.transaccion, self.protocolo,
                           self.longitud, self.unidad)

    @classmethod
    def desde_bytes(cls, datos):
        if len(datos) != cls.TAMANO:
            raise ErrorProtocolo(f"la cabecera MBAP debe tener 7 bytes, tiene {len(datos)}")
        transaccion, protocolo, longitud, unidad = struct.unpack(cls.FORMATO, datos)
        return cls(transaccion, longitud, unidad, protocolo)

    def __repr__(self):
        return (f"CabeceraMBAP(transaccion={self.transaccion}, protocolo={self.protocolo}, "
                f"longitud={self.longitud}, unidad={self.unidad})")


# ---------------------------------------------------------------------------
# Peticiones (una clase por función)
# ---------------------------------------------------------------------------

class Peticion:
    """Clase base. Cada subclase define CODIGO, pdu() y procesar_respuesta()."""

    CODIGO = None

    def pdu(self):
        """PDU de la petición: código de función + datos."""
        return bytes([self.CODIGO]) + self._datos()

    def _datos(self):
        raise NotImplementedError

    def procesar_respuesta(self, datos):
        """Recibe los datos de la respuesta (PDU sin el código de función),
        los valida y devuelve el resultado. Lanza ErrorProtocolo si no cuadran.
        """
        raise NotImplementedError

    @staticmethod
    def _comprobar_longitud(datos, esperada, que):
        if len(datos) != esperada:
            raise ErrorProtocolo(
                f"{que}: se esperaban {esperada} bytes de datos y llegaron {len(datos)}")


class _Lectura(Peticion):
    """Petición común de 01, 02, 03 y 04: dirección inicial + cantidad.

    La respuesta es: número de bytes N + N bytes de datos.
    """

    MAXIMO = None   # cantidad máxima de elementos por petición

    def __init__(self, direccion, cantidad):
        _comprobar_entero(direccion, 0, 0xFFFF, "dirección")
        _comprobar_entero(cantidad, 1, self.MAXIMO, "cantidad")
        self.direccion = direccion
        self.cantidad = cantidad

    def _datos(self):
        return struct.pack(">HH", self.direccion, self.cantidad)

    def _bytes_esperados(self):
        raise NotImplementedError

    def _decodificar(self, valores):
        raise NotImplementedError

    def procesar_respuesta(self, datos):
        if not datos:
            raise ErrorProtocolo("respuesta sin número de bytes")
        n = datos[0]
        esperados = self._bytes_esperados()
        if n != esperados:
            raise ErrorProtocolo(f"número de bytes {n}, se esperaba {esperados}")
        self._comprobar_longitud(datos, 1 + n, "lectura")
        return self._decodificar(datos[1:])


class _LecturaBits(_Lectura):
    """01 y 02: los bits llegan empaquetados, 8 por byte."""

    MAXIMO = 2000

    def _bytes_esperados(self):
        return (self.cantidad + 7) // 8

    def _decodificar(self, valores):
        return desempaquetar_bits(valores, self.cantidad)


class _LecturaRegistros(_Lectura):
    """03 y 04: cada registro son 2 bytes, primero el más significativo."""

    MAXIMO = 125

    def _bytes_esperados(self):
        return 2 * self.cantidad

    def _decodificar(self, valores):
        return list(struct.unpack(f">{self.cantidad}H", valores))


class LeerCoils(_LecturaBits):
    """01 · Read Coils."""
    CODIGO = FC_LEER_COILS


class LeerEntradasDiscretas(_LecturaBits):
    """02 · Read Discrete Inputs."""
    CODIGO = FC_LEER_ENTRADAS_DISCRETAS


class LeerHoldingRegisters(_LecturaRegistros):
    """03 · Read Holding Registers."""
    CODIGO = FC_LEER_HOLDING_REGISTERS


class LeerInputRegisters(_LecturaRegistros):
    """04 · Read Input Registers."""
    CODIGO = FC_LEER_INPUT_REGISTERS


class _EscrituraSimple(Peticion):
    """05 y 06: dirección + valor de 16 bits. La respuesta es un eco exacto."""

    def __init__(self, direccion, valor_trama):
        _comprobar_entero(direccion, 0, 0xFFFF, "dirección")
        self.direccion = direccion
        self.valor_trama = valor_trama   # valor de 16 bits tal como viaja

    def _datos(self):
        return struct.pack(">HH", self.direccion, self.valor_trama)

    def procesar_respuesta(self, datos):
        if datos != self._datos():
            raise ErrorProtocolo(
                f"el eco no coincide: enviado {a_hex(self._datos())}, recibido {a_hex(datos)}")
        return None


class EscribirCoil(_EscrituraSimple):
    """05 · Write Single Coil. ON viaja como FF 00 y OFF como 00 00."""

    CODIGO = FC_ESCRIBIR_COIL

    def __init__(self, direccion, valor):
        self.valor = _a_bit(valor)
        super().__init__(direccion, COIL_ON if self.valor else COIL_OFF)


class EscribirRegistro(_EscrituraSimple):
    """06 · Write Single Register. Admite -32768..65535 (negativos en C2)."""

    CODIGO = FC_ESCRIBIR_REGISTRO

    def __init__(self, direccion, valor):
        super().__init__(direccion, a_registro(valor))
        self.valor = valor


class _EscrituraMultiple(Peticion):
    """0F y 10: dirección + cantidad + número de bytes + valores.

    La respuesta confirma solo la dirección y la cantidad escritas.
    """

    MAXIMO = None

    def __init__(self, direccion, valores):
        _comprobar_entero(direccion, 0, 0xFFFF, "dirección")
        valores = list(valores)
        if not 1 <= len(valores) <= self.MAXIMO:
            raise ValueError(f"cantidad fuera de rango (1-{self.MAXIMO}): {len(valores)}")
        self.direccion = direccion
        self.cantidad = len(valores)

    def _valores_bytes(self):
        raise NotImplementedError

    def _datos(self):
        valores = self._valores_bytes()
        return struct.pack(">HHB", self.direccion, self.cantidad, len(valores)) + valores

    def procesar_respuesta(self, datos):
        self._comprobar_longitud(datos, 4, "confirmación de escritura")
        direccion, cantidad = struct.unpack(">HH", datos)
        if (direccion, cantidad) != (self.direccion, self.cantidad):
            raise ErrorProtocolo(
                f"confirmación incorrecta: dirección {direccion}, cantidad {cantidad} "
                f"(se enviaron {self.direccion}, {self.cantidad})")
        return None


class EscribirCoils(_EscrituraMultiple):
    """0F · Write Multiple Coils. Bits empaquetados como en la 01."""

    CODIGO = FC_ESCRIBIR_COILS
    MAXIMO = 1968

    def __init__(self, direccion, valores):
        valores = [_a_bit(v) for v in valores]
        super().__init__(direccion, valores)
        self.valores = valores

    def _valores_bytes(self):
        return empaquetar_bits(self.valores)


class EscribirRegistros(_EscrituraMultiple):
    """10 · Write Multiple Registers. 2 bytes por registro, big-endian."""

    CODIGO = FC_ESCRIBIR_REGISTROS
    MAXIMO = 123

    def __init__(self, direccion, valores):
        valores = [a_registro(v) for v in valores]
        super().__init__(direccion, valores)
        self.valores = valores

    def _valores_bytes(self):
        return struct.pack(f">{self.cantidad}H", *self.valores)


class LeerEscribirRegistros(_LecturaRegistros):
    """17 · Read/Write Multiple Registers.

    El servidor escribe primero y lee después, en la misma transacción.
    La respuesta tiene el mismo formato que la de la función 03.
    """

    CODIGO = FC_LEER_ESCRIBIR_REGISTROS
    MAXIMO_ESCRITURA = 121

    def __init__(self, direccion_lectura, cantidad_lectura, direccion_escritura, valores):
        super().__init__(direccion_lectura, cantidad_lectura)
        _comprobar_entero(direccion_escritura, 0, 0xFFFF, "dirección de escritura")
        valores = [a_registro(v) for v in valores]
        if not 1 <= len(valores) <= self.MAXIMO_ESCRITURA:
            raise ValueError(
                f"cantidad de escritura fuera de rango (1-{self.MAXIMO_ESCRITURA}): {len(valores)}")
        self.direccion_escritura = direccion_escritura
        self.valores = valores

    def _datos(self):
        n = len(self.valores)
        return (struct.pack(">HHHHB", self.direccion, self.cantidad,
                            self.direccion_escritura, n, 2 * n)
                + struct.pack(f">{n}H", *self.valores))


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

class ClienteModbusTCP:
    """Cliente Modbus TCP.

    ip, puerto  Dirección del servidor (el simulador en Linux usa el 5020).
    unidad      Unit ID que se pone en la cabecera.
    timeout     Segundos de espera para conectar y para cada respuesta.
    traza       Si es True, imprime cada trama enviada (TX) y recibida (RX).

    Se puede usar con `with`; ejecutar() conecta automáticamente si hace falta.
    """

    def __init__(self, ip="127.0.0.1", puerto=PUERTO_MODBUS, unidad=UNIDAD_POR_DEFECTO,
                 timeout=TIMEOUT_POR_DEFECTO, traza=False, salida_traza=None):
        _comprobar_entero(puerto, 1, 65535, "puerto")
        _comprobar_entero(unidad, 0, 255, "unidad")
        if timeout <= 0:
            raise ValueError(f"el timeout debe ser positivo: {timeout}")
        self.ip = ip
        self.puerto = puerto
        self.unidad = unidad
        self.timeout = timeout
        self.traza = traza
        self.salida_traza = salida_traza
        self._socket = None
        self._transaccion = 0

    # --- Conexión ---------------------------------------------------------

    def conectar(self):
        """Abre la conexión TCP. Lanza ErrorConexion si no es posible."""
        if self._socket is not None:
            return
        try:
            self._socket = socket.create_connection((self.ip, self.puerto), self.timeout)
        except socket.timeout as e:
            raise ErrorConexion(f"no se pudo conectar a {self.ip}:{self.puerto} (timeout)") from e
        except OSError as e:
            raise ErrorConexion(f"no se pudo conectar a {self.ip}:{self.puerto} ({e})") from e
        self._socket.settimeout(self.timeout)

    def cerrar(self):
        """Cierra la conexión (no hace nada si ya estaba cerrada)."""
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    @property
    def conectado(self):
        return self._socket is not None

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, *exc):
        self.cerrar()

    # --- Transacción ------------------------------------------------------

    def _siguiente_transaccion(self):
        """Transaction ID de 1 a 65535, vuelve a 1 al desbordar."""
        self._transaccion = self._transaccion % 0xFFFF + 1
        return self._transaccion

    def _trazar(self, sentido, trama):
        if self.traza:
            print(f"{sentido}: {a_hex(trama)}", file=self.salida_traza or sys.stdout)

    def _recibir_exacto(self, n):
        """Lee exactamente n bytes: TCP es un flujo y puede partir las tramas."""
        datos = bytearray()
        while len(datos) < n:
            trozo = self._socket.recv(n - len(datos))
            if not trozo:
                raise ErrorConexion("el servidor cerró la conexión")
            datos += trozo
        return bytes(datos)

    def _recibir_trama(self):
        """Lee la cabecera (7 bytes) y después exactamente Length - 1 bytes."""
        cabecera_bytes = self._recibir_exacto(CabeceraMBAP.TAMANO)
        cabecera = CabeceraMBAP.desde_bytes(cabecera_bytes)
        if cabecera.protocolo != 0:
            raise ErrorProtocolo(f"Protocol ID {cabecera.protocolo}, se esperaba 0")
        if not 2 <= cabecera.longitud <= CabeceraMBAP.LONGITUD_MAXIMA:
            raise ErrorProtocolo(f"Length imposible: {cabecera.longitud}")
        pdu = self._recibir_exacto(cabecera.longitud - 1)
        self._trazar("RX", cabecera_bytes + pdu)
        return cabecera, pdu

    def ejecutar(self, peticion):
        """Envía una Peticion y devuelve el resultado ya interpretado.

        Lanza ExcepcionModbus, ErrorTimeout, ErrorConexion o ErrorProtocolo.
        Tras un timeout o un error de protocolo cierra la conexión (el flujo
        TCP puede haber quedado desalineado); la siguiente llamada reconecta.
        """
        self.conectar()
        pdu = peticion.pdu()
        transaccion = self._siguiente_transaccion()
        trama = CabeceraMBAP.para_pdu(transaccion, self.unidad, pdu).a_bytes() + pdu
        try:
            self._trazar("TX", trama)
            self._socket.sendall(trama)
            while True:
                cabecera, pdu_respuesta = self._recibir_trama()
                if cabecera.transaccion == transaccion:
                    break
                # Respuesta atrasada de una petición anterior: se descarta.
            return self._interpretar(peticion, cabecera, pdu_respuesta)
        except socket.timeout as e:
            self.cerrar()
            raise ErrorTimeout(f"sin respuesta en {self.timeout} s") from e
        except ErrorConexion:
            self.cerrar()
            raise
        except ErrorProtocolo:
            self.cerrar()
            raise
        except OSError as e:
            self.cerrar()
            raise ErrorConexion(str(e)) from e

    def _interpretar(self, peticion, cabecera, pdu):
        if cabecera.unidad != self.unidad:
            raise ErrorProtocolo(f"Unit ID {cabecera.unidad}, se esperaba {self.unidad}")
        funcion = pdu[0]
        if funcion == peticion.CODIGO | 0x80:
            if len(pdu) != 2:
                raise ErrorProtocolo(f"respuesta de excepción de {len(pdu)} bytes, deben ser 2")
            raise ExcepcionModbus(peticion.CODIGO, pdu[1])
        if funcion != peticion.CODIGO:
            raise ErrorProtocolo(
                f"código de función 0x{funcion:02X}, se esperaba 0x{peticion.CODIGO:02X}")
        return peticion.procesar_respuesta(pdu[1:])

    # --- Atajos -----------------------------------------------------------

    def leer_coils(self, direccion, cantidad):
        """01 -> lista de booleanos."""
        return self.ejecutar(LeerCoils(direccion, cantidad))

    def leer_entradas_discretas(self, direccion, cantidad):
        """02 -> lista de booleanos."""
        return self.ejecutar(LeerEntradasDiscretas(direccion, cantidad))

    def leer_holding_registers(self, direccion, cantidad):
        """03 -> lista de enteros sin signo (0-65535)."""
        return self.ejecutar(LeerHoldingRegisters(direccion, cantidad))

    def leer_input_registers(self, direccion, cantidad):
        """04 -> lista de enteros sin signo (0-65535)."""
        return self.ejecutar(LeerInputRegisters(direccion, cantidad))

    def escribir_coil(self, direccion, valor):
        """05 · valor True/False o 1/0."""
        return self.ejecutar(EscribirCoil(direccion, valor))

    def escribir_registro(self, direccion, valor):
        """06 · valor -32768..65535."""
        return self.ejecutar(EscribirRegistro(direccion, valor))

    def escribir_coils(self, direccion, valores):
        """0F · lista de True/False o 1/0."""
        return self.ejecutar(EscribirCoils(direccion, valores))

    def escribir_registros(self, direccion, valores):
        """10 · lista de valores -32768..65535."""
        return self.ejecutar(EscribirRegistros(direccion, valores))

    def leer_escribir_registros(self, direccion_lectura, cantidad_lectura,
                                direccion_escritura, valores):
        """17 · escribe `valores` y después lee; devuelve lo leído."""
        return self.ejecutar(LeerEscribirRegistros(
            direccion_lectura, cantidad_lectura, direccion_escritura, valores))

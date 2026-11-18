"""
modbus_cli.py - Ejecuta una sola petición Modbus TCP con la biblioteca modbus_tcp.

Uso:
    python3 modbus_cli.py [--ip IP] [--puerto P] [--unidad U] [--timeout S] [--traza]
                          ORDEN ARGUMENTOS

Órdenes:
    leer-coils         DIRECCION CANTIDAD
    leer-entradas      DIRECCION CANTIDAD
    leer-holding       DIRECCION CANTIDAD
    leer-input         DIRECCION CANTIDAD
    escribir-coil      DIRECCION VALOR            (0/1, on/off)
    escribir-registro  DIRECCION VALOR            (-32768..65535)
    escribir-coils     DIRECCION VALOR [VALOR ...]
    escribir-registros DIRECCION VALOR [VALOR ...]
    leer-escribir      DIR_LECTURA CANTIDAD DIR_ESCRITURA VALOR [VALOR ...]

Los números se pueden escribir en decimal o en hexadecimal (0x6B).
La traza (--traza) se escribe en la salida de error para no mezclarse con el resultado.

Salida y códigos de salida:
    lectura de bits        "direccion valor" por línea                    0
    lectura de registros   "direccion sin_signo con_signo 0xHHHH"         0
    escritura correcta     OK                                             0
    argumento inválido     ARGUMENTO INVALIDO: <motivo>                   2
    excepción Modbus       EXCEPCION 0xCC NOMBRE                          3
    timeout                TIMEOUT                                        4
    error de conexión      ERROR DE CONEXION: <motivo>                    5
    error de protocolo     ERROR DE PROTOCOLO: <motivo>                   6
"""

import argparse
import sys

from modbus_tcp import (
    ClienteModbusTCP, ExcepcionModbus, ErrorTimeout, ErrorConexion, ErrorProtocolo,
    LeerCoils, LeerEntradasDiscretas, LeerHoldingRegisters, LeerInputRegisters,
    EscribirCoil, EscribirRegistro, EscribirCoils, EscribirRegistros,
    LeerEscribirRegistros, a_con_signo, UNIDAD_POR_DEFECTO, TIMEOUT_POR_DEFECTO,
)

PUERTO_CLI = 5020   # Puerto del simulador del laboratorio en Linux

SALIDA_OK = 0
SALIDA_ARGUMENTO = 2
SALIDA_EXCEPCION = 3
SALIDA_TIMEOUT = 4
SALIDA_CONEXION = 5
SALIDA_PROTOCOLO = 6


class ArgumentoInvalido(Exception):
    pass


class Parser(argparse.ArgumentParser):
    """ArgumentParser que informa de los errores con el formato de la práctica."""

    def error(self, mensaje):
        raise ArgumentoInvalido(mensaje)


def entero(texto):
    """Entero en decimal o hexadecimal (0x..), con signo opcional."""
    try:
        return int(texto, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"no es un número: {texto}")


def bit(texto):
    """Valor de coil: 0/1, on/off, true/false."""
    t = texto.lower()
    if t in ("1", "on", "true"):
        return True
    if t in ("0", "off", "false"):
        return False
    raise argparse.ArgumentTypeError(f"valor de coil inválido (0/1, on/off): {texto}")


# Cada orden: (tipo de resultado, constructor de la petición a partir de los argumentos)
def _ordenes():
    return {
        "leer-coils": ("bits", lambda a: LeerCoils(a.direccion, a.cantidad)),
        "leer-entradas": ("bits", lambda a: LeerEntradasDiscretas(a.direccion, a.cantidad)),
        "leer-holding": ("registros", lambda a: LeerHoldingRegisters(a.direccion, a.cantidad)),
        "leer-input": ("registros", lambda a: LeerInputRegisters(a.direccion, a.cantidad)),
        "escribir-coil": ("escritura", lambda a: EscribirCoil(a.direccion, a.valor)),
        "escribir-registro": ("escritura", lambda a: EscribirRegistro(a.direccion, a.valor)),
        "escribir-coils": ("escritura", lambda a: EscribirCoils(a.direccion, a.valores)),
        "escribir-registros": ("escritura", lambda a: EscribirRegistros(a.direccion, a.valores)),
        "leer-escribir": ("registros", lambda a: LeerEscribirRegistros(
            a.direccion, a.cantidad, a.direccion_escritura, a.valores)),
    }


def crear_parser():
    p = Parser(prog="modbus_cli.py", description="Cliente Modbus TCP de línea de órdenes")
    p.add_argument("--ip", default="127.0.0.1")
    p.add_argument("--puerto", type=entero, default=PUERTO_CLI)
    p.add_argument("--unidad", type=entero, default=UNIDAD_POR_DEFECTO)
    p.add_argument("--timeout", type=float, default=TIMEOUT_POR_DEFECTO)
    p.add_argument("--traza", action="store_true", help="mostrar las tramas TX/RX")

    sub = p.add_subparsers(dest="orden", metavar="ORDEN", parser_class=Parser)
    sub.required = True

    for nombre in ("leer-coils", "leer-entradas", "leer-holding", "leer-input"):
        s = sub.add_parser(nombre)
        s.add_argument("direccion", type=entero)
        s.add_argument("cantidad", type=entero)

    s = sub.add_parser("escribir-coil")
    s.add_argument("direccion", type=entero)
    s.add_argument("valor", type=bit)

    s = sub.add_parser("escribir-registro")
    s.add_argument("direccion", type=entero)
    s.add_argument("valor", type=entero)

    s = sub.add_parser("escribir-coils")
    s.add_argument("direccion", type=entero)
    s.add_argument("valores", type=bit, nargs="+")

    s = sub.add_parser("escribir-registros")
    s.add_argument("direccion", type=entero)
    s.add_argument("valores", type=entero, nargs="+")

    s = sub.add_parser("leer-escribir")
    s.add_argument("direccion", type=entero, help="dirección de lectura")
    s.add_argument("cantidad", type=entero, help="cantidad a leer")
    s.add_argument("direccion_escritura", type=entero)
    s.add_argument("valores", type=entero, nargs="+")
    return p


def imprimir_resultado(tipo, direccion, resultado):
    if tipo == "bits":
        for i, v in enumerate(resultado):
            print(f"{direccion + i} {int(v)}")
    elif tipo == "registros":
        for i, v in enumerate(resultado):
            print(f"{direccion + i} {v} {a_con_signo(v)} 0x{v:04X}")
    else:
        print("OK")


def main(argv=None):
    try:
        args = crear_parser().parse_args(argv)
        tipo, construir = _ordenes()[args.orden]
        peticion = construir(args)
        cliente = ClienteModbusTCP(args.ip, args.puerto, args.unidad, args.timeout,
                                   traza=args.traza, salida_traza=sys.stderr)
    except (ArgumentoInvalido, ValueError) as e:
        print(f"ARGUMENTO INVALIDO: {e}")
        return SALIDA_ARGUMENTO

    try:
        with cliente:
            resultado = cliente.ejecutar(peticion)
    except ExcepcionModbus as e:
        print(f"EXCEPCION 0x{e.codigo:02X} {e.nombre}")
        return SALIDA_EXCEPCION
    except ErrorTimeout:
        print("TIMEOUT")
        return SALIDA_TIMEOUT
    except ErrorConexion as e:
        print(f"ERROR DE CONEXION: {e}")
        return SALIDA_CONEXION
    except ErrorProtocolo as e:
        print(f"ERROR DE PROTOCOLO: {e}")
        return SALIDA_PROTOCOLO

    imprimir_resultado(tipo, args.direccion, resultado)
    return SALIDA_OK


if __name__ == "__main__":
    sys.exit(main())

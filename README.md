# Cliente Modbus TCP escrito a mano

Práctica 1 de Modbus TCP · Redes de Comunicaciones Industriales · Universidad de Deusto
**Autor:** Manuel Narváez · **Curso:** 2026–2027

Cliente Modbus TCP completo escrito byte a byte en Python, solo con la biblioteca estándar
(`socket` y `struct`), sin bibliotecas Modbus. Implementa los niveles básico y avanzado:

| Código | Función | Clase | Atajo del cliente |
|---|---|---|---|
| 01 | Read Coils | `LeerCoils` | `leer_coils(dir, cant)` |
| 02 | Read Discrete Inputs | `LeerEntradasDiscretas` | `leer_entradas_discretas(dir, cant)` |
| 03 | Read Holding Registers | `LeerHoldingRegisters` | `leer_holding_registers(dir, cant)` |
| 04 | Read Input Registers | `LeerInputRegisters` | `leer_input_registers(dir, cant)` |
| 05 | Write Single Coil | `EscribirCoil` | `escribir_coil(dir, valor)` |
| 06 | Write Single Register | `EscribirRegistro` | `escribir_registro(dir, valor)` |
| 0F | Write Multiple Coils | `EscribirCoils` | `escribir_coils(dir, valores)` |
| 10 | Write Multiple Registers | `EscribirRegistros` | `escribir_registros(dir, valores)` |
| 17 | Read/Write Multiple Registers | `LeerEscribirRegistros` | `leer_escribir_registros(dl, cl, de, valores)` |

Errores: `ExcepcionModbus`, `ErrorTimeout`, `ErrorConexion` y `ErrorProtocolo`, todos derivados de `ErrorModbus`.

## Estructura

```
practica1/
  modbus_tcp.py    biblioteca (CabeceraMBAP, peticiones, ClienteModbusTCP, errores)
  modbus_cli.py    programa de línea de órdenes
pruebas/
  test_tramas.py   comprueba cada trama TX/RX de ejemplo del guion, sin simulador
```

La carpeta `practica1/` es la que se entrega.

### Diseño de la biblioteca

- **`CabeceraMBAP`**: los 7 bytes de cabecera (`a_bytes()` / `desde_bytes()`).
- **`Peticion`**: clase base. Cada función define `pdu()` (lo que se envía) y `procesar_respuesta()` (valida y decodifica lo recibido).
  Las clases intermedias `_LecturaBits`, `_LecturaRegistros`, `_EscrituraSimple` y `_EscrituraMultiple` agrupan lo común, así que cada función concreta ocupa unas pocas líneas.
- **`ClienteModbusTCP`**: numera las transacciones, añade la MBAP, envía con `sendall()`, lee primero 7 bytes y después exactamente `Length − 1` (TCP puede partir las tramas) y comprueba Transaction ID, Protocol ID, Unit ID y código de función. Tras un timeout o un error de protocolo cierra la conexión; la siguiente petición reconecta sola.

## Puesta en marcha (Ubuntu, laboratorio)

```bash
chmod +x simulador_modbus
./simulador_modbus                 # sin root arranca en el puerto 5020
```

En otra terminal:

```bash
cd practica1
python3 modbus_cli.py --puerto 5020 --traza leer-holding 107 3
```

Wireshark: interfaz `lo`, filtro `tcp.port == 5020` y *Analizar › Decodificar como…* → TCP 5020 = Modbus/TCP.
Filtros útiles una vez decodificado:

| Filtro | Qué muestra |
|---|---|
| `mbtcp` | Todo el tráfico Modbus TCP |
| `modbus.func_code == 16` | Escrituras de varios registros |
| `modbus.exception_code` | Respuestas de excepción |
| `mbtcp.trans_id == 3` | Una transacción concreta |
| `tcp.port == 5020 && tcp.flags.reset == 1` | Conexiones rechazadas o cortadas |

## Uso desde la consola de Python

```python
>>> from modbus_tcp import *
>>> c = ClienteModbusTCP("127.0.0.1", 5020, traza=True)
>>> c.leer_holding_registers(107, 3)          # lectura
TX: 00 01 00 00 00 06 01 03 00 6B 00 03
RX: 00 01 00 00 00 09 01 03 06 02 2B 00 00 00 64
[555, 0, 100]
>>> c.escribir_registro(1, 3)                 # escritura
>>> c.leer_holding_registers(9999, 2)         # excepción
ExcepcionModbus: Excepción 0x02 (ILLEGAL DATA ADDRESS) en la función 0x03
>>> c.cerrar()
```

## `modbus_cli.py`

```
python3 modbus_cli.py [--ip IP] [--puerto P] [--unidad U] [--timeout S] [--traza] ORDEN ARGUMENTOS
```

| Orden | Argumentos |
|---|---|
| `leer-coils`, `leer-entradas`, `leer-holding`, `leer-input` | dirección cantidad |
| `escribir-coil`, `escribir-registro` | dirección valor |
| `escribir-coils`, `escribir-registros` | dirección valores… |
| `leer-escribir` | dir_lectura cantidad dir_escritura valores… |

Valores por defecto: IP `127.0.0.1`, puerto `5020`, unidad `1`, timeout `3` s. Los números se pueden dar en decimal o en hexadecimal (`0x6B`). La traza se escribe en la salida de error para no mezclarse con el resultado.

| Caso | Salida | Código |
|---|---|---|
| Lectura de bits | `direccion valor` | 0 |
| Lectura de registros | `direccion sin_signo con_signo 0xHHHH` | 0 |
| Escritura correcta | `OK` | 0 |
| Argumento fuera de rango | `ARGUMENTO INVALIDO: <motivo>` | 2 |
| Excepción Modbus | `EXCEPCION 0xCC NOMBRE` | 3 |
| Timeout | `TIMEOUT` | 4 |
| Error de conexión | `ERROR DE CONEXION: <motivo>` | 5 |
| Error de protocolo | `ERROR DE PROTOCOLO: <motivo>` | 6 |

### Cómo provocar cada error con el simulador

- **Excepción**: `leer-holding 9999 2`, o deshabilitar una función en *Comportamiento*.
- **Timeout**: en *Comportamiento*, atender solo al Unit ID 1 con la política *Descartar*, y usar `--unidad 7`.
- **Conexión**: `--puerto 5999` (nadie escucha).

## Pruebas

```bash
python3 -m unittest discover -s pruebas -v
```

Un servidor falso local comprueba que cada trama enviada es exactamente la TX del guion y responde con la RX; también se prueban timeout, conexión rechazada, eco incorrecto, respuestas partidas en dos `recv()` y argumentos fuera de rango.

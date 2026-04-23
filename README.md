# Informe: Coordinación y Escalabilidad del Sistema de Stock de Verdulería

## Arquitectura general

El sistema procesa pares `(fruta, cantidad)` enviados por múltiples clientes concurrentes a través de un pipeline distribuido: **Gateway → Sum → Aggregation → Joiner → Gateway**. La comunicación interna se realiza mediante RabbitMQ, usando colas de trabajo (*work queues*) para distribuir carga y exchanges directos para enrutar mensajes a instancias específicas.

Todos los mensajes internos siguen el formato `[client_id, payload]`, donde `client_id` identifica unívocamente al cliente que originó la consulta. Esto permite que cada componente mantenga estado aislado por cliente y procese múltiples consultas concurrentemente sin interferencia.

---

## Coordinación de instancias Sum

### Distribución de datos

Los datos de todos los clientes se publican en una única cola compartida entre todas las instancias de Sum. RabbitMQ distribuye los mensajes en round-robin, de modo que cada instancia procesa una fracción del dataset de cada cliente. Cada instancia acumula sus resultados parciales en un diccionario `amount_by_fruit[client_id]`, aislando el estado por cliente.

### Problema del EOF

Cuando un cliente termina de enviar datos, el Gateway publica un único mensaje EOF en la cola compartida. Este mensaje incluye la cantidad total de registros enviados (`total_messages`). Solo una instancia de Sum recibe este EOF, pero todas tienen datos parciales que deben ser enviados al Aggregator.

### Protocolo de coordinación entre instancias Sum

Para resolver este problema, las instancias Sum se comunican entre sí mediante un exchange directo donde cada instancia tiene su propia cola de entrada (`SUM_EOF_EXCHANGE_{ID}`). El protocolo opera en tres fases:

1. **EOF_RECEIVED** `[total_messages]`: La instancia que recibe el EOF del Gateway actúa como coordinadora y publica este mensaje a todas las instancias (incluyendo a sí misma), indicando cuántos mensajes envió el cliente en total.

2. **COUNT_RESPONSE** `[my_count, sender_id]`: Cada instancia responde con la cantidad de mensajes que procesó para ese cliente hasta el momento.

3. **EOF_CONFIRMED** `[]`: Una vez que el coordinador acumula `SUM_AMOUNT` respuestas y verifica que su suma es igual a `total_messages`, publica este mensaje a todas las instancias. Si la suma no coincide, el coordinador espera un intervalo configurable y repite la consulta desde el paso 1, dado que alguna instancia todavía puede tener mensajes en vuelo.

Al recibir EOF_CONFIRMED, cada instancia envía sus resultados parciales al Aggregator junto con un EOF propio, y libera el estado del cliente.

### Enrutamiento hacia Aggregators

Cuando hay múltiples instancias de Aggregation, cada instancia de Sum enruta cada fruta siempre al mismo Aggregator usando `hash_md5(fruta) % AGGREGATION_AMOUNT`. Se usa MD5 en lugar del `hash()` nativo de Python porque este último es no determinístico entre procesos (PYTHONHASHSEED), lo que provocaría que la misma fruta sea enviada a Aggregators distintos según qué instancia de Sum la procese.

---

## Coordinación de instancias Aggregation

Cada instancia de Aggregation recibe un subconjunto disjunto de frutas (determinado por el hash) de todas las instancias de Sum. Mantiene el estado `fruit_tops[client_id]` y `eof_counts[client_id]` por cliente.

Al recibir un EOF de una instancia Sum, incrementa el contador de EOFs para ese cliente. Recién cuando acumula `SUM_AMOUNT` EOFs — uno por cada instancia de Sum — calcula el top parcial de sus frutas y lo envía al Joiner. Este mecanismo garantiza que el Aggregator no calcule su top antes de haber recibido todos los datos.

El top parcial enviado tiene tamaño `TOP_SIZE`, suficiente para que el Joiner pueda calcular el top global correcto al mergear los tops de todas las instancias.

---

## Coordinación del Joiner

El Joiner recibe un top parcial de cada instancia de Aggregation para cada cliente. Acumula estos tops en `partial_tops[client_id]` y espera `AGGREGATION_AMOUNT` mensajes antes de calcular el top final. El merge consiste en combinar todas las listas parciales, ordenarlas por cantidad descendente y tomar los primeros `TOP_SIZE` elementos. El resultado se envía al Gateway junto con el `client_id` para que este pueda entregarlo al cliente correcto.

---

## Escalabilidad respecto a los clientes

El sistema escala horizontalmente respecto a los clientes sin cambios estructurales. El Gateway asigna un `client_id` incremental a cada conexión entrante y lo propaga en todos los mensajes internos. Cada componente (Sum, Aggregation, Joiner) mantiene estado aislado por `client_id`, por lo que múltiples clientes pueden ser procesados concurrentemente sin interferencia. El Gateway maneja cada cliente en un proceso independiente del pool de procesos, y un proceso adicional escucha los resultados y los despacha al cliente correcto usando el `client_id`.

---

## Escalabilidad respecto a la cantidad de controles

**Más instancias de Sum**: El protocolo de coordinación por exchange se adapta automáticamente. El coordinador espera exactamente `SUM_AMOUNT` COUNT_RESPONSE antes de confirmar, y cada Aggregator espera `SUM_AMOUNT` EOFs. Ambos valores se leen de variables de entorno.

**Más instancias de Aggregation**: La función de hash garantiza que la misma fruta siempre vaya al mismo Aggregator independientemente de cuántas instancias haya. El Joiner espera `AGGREGATION_AMOUNT` tops parciales, también configurado por variable de entorno.

---

## Modelo de concurrencia y justificación del uso de threads

Cada instancia de Sum corre con tres threads:

- **Hilo principal**: consume la cola de datos compartida, procesa registros y detecta EOFs del Gateway.
- **Hilo EOF**: consume el exchange de coordinación entre instancias Sum, procesando mensajes EOF_RECEIVED, COUNT_RESPONSE y EOF_CONFIRMED.
- **Hilo coordinador** (efímero): se crea cuando llega un EOF del Gateway, ejecuta el protocolo de coordinación con reintentos y termina al confirmar EOF_CONFIRMED. Pueden existir múltiples simultáneamente si varios clientes terminan al mismo tiempo.

Dado que Python tiene el GIL (*Global Interpreter Lock*), solo un thread ejecuta bytecode Python en un momento dado. Sin embargo, el GIL se libera durante operaciones de I/O bloqueantes, que es exactamente el caso de todos estos threads: pasan la mayor parte del tiempo esperando mensajes de RabbitMQ o durmiendo entre reintentos. En este escenario los threads son la solución correcta, ya que el cuello de botella es I/O y no CPU, y el GIL no penaliza el paralelismo real del sistema.

---

# Trabajo Práctico - Coordinación

En este trabajo se busca familiarizar a los estudiantes con los desafíos de la coordinación del trabajo y el control de la complejidad en sistemas distribuidos. Para tal fin se provee un esqueleto de un sistema de control de stock de una verdulería y un conjunto de escenarios de creciente grado de complejidad y distribución que demandarán mayor sofisticación en la comunicación de las partes involucradas.

## Ejecución

`make up` : Inicia los contenedores del sistema y comienza a seguir los logs de todos ellos en un solo flujo de salida.

`make down`:   Detiene los contenedores y libera los recursos asociados.

`make logs`: Sigue los logs de todos los contenedores en un solo flujo de salida.

`make test`: Inicia los contenedores del sistema, espera a que los clientes finalicen, compara los resultados con una ejecución serial y detiene los contenederes.

`make switch`: Permite alternar rápidamente entre los archivos de docker compose de los distintos escenarios provistos.

## Elementos del sistema objetivo

![ ](./imgs/diagrama_de_robustez.jpg  "Diagrama de Robustez")
*Fig. 1: Diagrama de Robustez*

### Client

Lee un archivo de entrada y envía por TCP/IP pares (fruta, cantidad) al sistema.
Cuando finaliza el envío de datos, aguarda un top de pares (fruta, cantidad) y vuelca el resultado en un archivo de salida csv.
El criterio y tamaño del top dependen de la configuración del sistema. Por defecto se trata de un top 3 de frutas de acuerdo a la cantidad total almacenada.

### Gateway

Es el punto de entrada y salida del sistema. Intercambia mensajes con los clientes y las colas internas utilizando distintos protocolos.

### Sum
 
Recibe pares  (fruta, cantidad) y aplica la función Suma de la clase `FruitItem`. Por defecto esa suma es la canónica para los números enteros, ej:

`("manzana", 5) + ("manzana", 8) = ("manzana", 13)`

Pero su implementación podría modificarse.
Cuando se detecta el final de la ingesta de datos envía los pares (fruta, cantidad) totales a los Aggregators.

### Aggregator

Consolida los datos de las distintas instancias de Sum.
Cuando se detecta el final de la ingesta, se calcula un top parcial y se envía esa información al Joiner.

### Joiner

Recibe tops parciales de las instancias del Aggregator.
Cuando se detecta el final de la ingesta, se envía el top final hacia el gateway para ser entregado al cliente.

## Limitaciones del esqueleto provisto

La implementación base respeta la división de responsabilidades de los distintos controles y hace uso de la clase `FruitItem` como un elemento opaco, sin asumir la implementación de las funciones de Suma y Comparación.

No obstante, esta implementación no cubre los objetivos buscados tal y como es presentada. Entre sus falencias puede destactarse que:

 - No se implementa la interfaz del middleware. 
 - No se dividen los flujos de datos de los clientes más allá del Gateway, por lo que no se es capaz de resolver múltiples consultas concurrentemente.
 - No se implementan mecanismos de sincronización que permitan escalar los controles Sum y Aggregator. En particular:
   - Las instancias de Sum se dividen el trabajo, pero solo una de ellas recibe la notificación de finalización en la ingesta de datos.
   - Las instancias de Sum realizan _broadcast_ a todas las instancias de Aggregator, en lugar de agrupar los datos por algún criterio y evitar procesamiento redundante.
  - No se maneja la señal SIGTERM, con la salvedad de los clientes y el Gateway.

## Condiciones de Entrega

El código de este repositorio se agrupa en dos carpetas, una para Python y otra para Golang. Los estudiantes deberán elegir **sólo uno** de estos lenguajes y realizar una implementación que funcione correctamente ante cambios en la multiplicidad de los controles (archivo de docker compose), los archivos de entrada y las implementaciones de las funciones de Suma y Comparación del `FruitItem`.

![ ](./imgs/mutabilidad.jpg  "Mutabilidad de Elementos")
*Fig. 2: Elementos mutables e inmutables*

A modo de referencia, en la *Figura 2* se marcan en tonos oscuros los elementos que los estudiantes no deben alterar y en tonos claros aquellos sobre los que tienen libertad de decisión.
Al momento de la evaluación y ejecución de las pruebas se **descartarán** o **reemplazarán** :

- Los archivos de entrada de la carpeta `datasets`.
- El archivo docker compose principal y los de la carpeta `scenarios`.
- Todos los archivos Dockerfile.
- Todo el código del cliente.
- Todo el código del gateway, salvo `message_handler`.
- La implementación del protocolo de comunicación externo y `FruitItem`.

Redactar un breve informe explicando el modo en que se coordinan las instancias de Sum y Aggregation, así como el modo en el que el sistema escala respecto a los clientes y a la cantidad de controles.

# Informe de los cambios

Resumen sencillo de lo que se ha hecho en esta versión y de cómo queda ordenado todo.

## Qué se ha hecho

**1. Se han quitado todas las campañas y misiones que venían hechas.**
Antes la IA traía cinco "estilos" de campaña ya escritos (moderno, 1944, insurgencia...) y
cuatro misiones de ejemplo, y el enemigo de esas misiones seguía un guion fijo. Todo eso se ha
borrado. Ahora no existe ninguna campaña hasta que tú la pides.

**2. Las campañas solo las pides tú, como tú quieras.**
Escribes lo que quieres con tus palabras, por ejemplo:
"campaña de 1944 para volar un puente en Normandía, 4 misiones de 60 minutos".
La IA entiende cuántas misiones quieres y cuánto dura cada una. Si no lo dices, te lo pregunta.
La campaña tiene siempre **un único objetivo final** (en el ejemplo, volar el puente) y para
llegar a él hay que pasar por tantos pasos como misiones hayas pedido. Cada paso tiene su propio
objetivo y su propio problema: algo que se complica, un inconveniente, un enemigo que no se
esperaba.

**3. La campaña puede salir mal.**
Hay un "margen de error": se pueden encajar algunas derrotas (una por cada tres misiones), pero
no más. Algunos pasos son críticos: si se pierden, ya no hay forma de llegar al objetivo. La
última misión siempre es crítica. Además, entre misión y misión pueden pasar **imprevistos**
(un informante desaparece, el enemigo descubre el plan, llega un temporal...). Los graves
gastan margen de error o hacen que la siguiente misión sea a vida o muerte. Así que el objetivo
se puede cumplir o no, por un error o por mala suerte.

**4. Zeus lo hace absolutamente todo por su cuenta.**
Tú solo pides la campaña. Zeus (la IA que hace de director de juego):

- inventa la época, los bandos, cómo combaten, cómo hablan por radio y los indicativos;
- elige el mapa de ARMA 3 entre los que tienes instalados y la zona dentro de ese mapa;
- moldea el mapa poniendo el decorado: fortificaciones, puestos de control, minas, bombas
  trampa, barricadas, alambradas, ruinas, restos de vehículos, humo...;
- crea cada misión: dónde está cada grupo, quién lo manda, qué tiene que hacer cada bando, la
  hora y el tiempo que hace;
- deja preparadas sorpresas para mitad de misión (refuerzos enemigos, niebla, más decorado);
- dirige la partida mientras se juega, complicándola o dando un respiro según vaya;
- al terminar cada misión escribe lo que ha pasado como una historia y adapta la siguiente.

Zeus ya **no obedece** peticiones del chat: decide él. Los mensajes del chat los oyen los NPC,
que siguen contestando por radio, cada uno con su personalidad.

El decorado no es solo de adorno: en el simulador las minas hieren a quien las cruza, las
fortificaciones protegen, las barricadas frenan, las torres dejan ver más lejos y el humo tapa
la vista.

**5. Se ha repasado todo el código.**
Se ha revisado pieza a pieza. Entre lo que se ha corregido:

- las respuestas largas de Zeus se cortaban a medias (tenía un límite de texto demasiado
  pequeño) y por eso muchas veces acababa usando sus reglas de emergencia;
- al decir "5 misiones de 90 minutos" la IA entendía 18 minutos por misión;
- en misiones cortas los grupos empezaban tan lejos que no llegaban al objetivo a tiempo; ahora
  la distancia depende de lo que dura la misión;
- los asaltos eran imposibles de ganar; ahora están equilibrados y la dificultad sube o baja
  según cómo te vaya;
- se repetían apellidos e historias entre personajes;
- las órdenes por radio salían con frases raras ("Reconoced Destruir la guarnición...").

**6. Se ha eliminado lo innecesario y se ha comprobado que funciona.**
Se han quitado órdenes que ya no tienen sentido (misiones sueltas, autoentrenamiento con
misiones fijas, listados de perfiles y escenarios, chat fuera de misión) y el enemigo con guion.
Las pruebas automáticas se han rehecho para la forma nueva de funcionar (168 pruebas, todas
correctas salvo una que necesita el Ollama de verdad y aquí se omite) y se han jugado campañas completas de principio a fin.

**7. Base de datos en el mismo PC que la IA.**
Se guarda en la carpeta `BBDD`. Guarda la campaña, cada misión, todo lo que pasó en cada misión
(contactos, bajas, decisiones de cada NPC, radio, chat) y el **lore**: la historia de la campaña
contada misión a misión, quién ha caído, los imprevistos y el final. También guarda lo que la
IA aprende, que no se pierde aunque se borren campañas.

**8. Vigilante del espacio.**
Cada vez que se guarda algo se mira cuánto ocupa la base de datos y cuánto disco queda. Si se
acerca al límite te avisa; si se pasa, la IA entra en "modo ahorro" (guarda solo lo esencial) y
te recomienda borrar campañas terminadas. Con `arma3-ia espacio` ves lo que ocupa cada campaña.

**9. Al acabar una campaña se te pregunta si quieres borrarla** de la base de datos para ganar
espacio (y si quieres borrar también sus informes de texto, que ocupan muy poco).

**10. Informes en texto.** Después de cada misión se escribe un informe corto (resultado, qué
pasó, la crónica de Zeus y qué cambia en la campaña). Al terminar la campaña se escribe un
informe final con el resultado, las misiones, la historia completa y quién sigue vivo.

## Cómo queda ordenado

```
ia-arma3/
├── BBDD/                      La base de datos (en tu PC, no se sube a internet)
│   └── ia-arma3.db
├── CAMPAÑAS/                  Una carpeta por cada campaña que pidas
│   └── operacion_.../
│       ├── campana.txt        Ficha: lo que pediste, el objetivo, los pasos, los personajes
│       ├── informe_final.txt  El resumen cuando la campaña termina
│       └── misiones/
│           ├── 01_.../
│           │   ├── mision.yaml   Lo que preparó Zeus (mapa, decorado, grupos, sorpresas)
│           │   └── informe.txt   El informe corto de esa misión
│           └── 02_.../
├── config/                    Tu configuración (qué modelo usar, qué mapas tienes...)
├── src/arma3_ia/              El programa
│   ├── zeus/                  El director de juego
│   ├── agentes/ y mando.py    Los NPC
│   ├── memoria/               La base de datos y el vigilante del espacio
│   ├── archivo.py             Los informes de la carpeta CAMPAÑAS
│   └── simulador/             El juego de texto para probar sin ARMA 3
├── tests/                     Las pruebas automáticas
├── docs/                      Explicaciones técnicas
└── scripts/                   El instalador para Ubuntu
```

El recorrido de una campaña es este:

1. Pides la campaña → Zeus la inventa → se guarda en `BBDD` y se crea su carpeta en `CAMPAÑAS`
   con la ficha.
2. `arma3-ia campana jugar` → Zeus prepara la misión (se guarda `mision.yaml`) → se juega con
   Zeus dirigiendo y los NPC decidiendo → Zeus la cierra y escribe la crónica → se guarda todo
   en `BBDD` y se escribe `informe.txt`.
3. Así misión tras misión hasta que se cumple el objetivo o la campaña fracasa → se escribe
   `informe_final.txt` → se te pregunta si quieres borrarla para ganar espacio.

## Lo que sigue pendiente

Todo esto funciona ya en el simulador de texto. El siguiente paso es el **puente con ARMA 3**,
para que lo que prepara Zeus (mapa, decorado, grupos, sorpresas) aparezca de verdad en el
juego. Está ya descrito en `docs/protocolo-puente.md`.

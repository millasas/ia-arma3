"""arma3-ia: inteligencia artificial local para ARMA 3.

Tú pides una campaña y Zeus (el director de juego) hace todo lo demás: elige el terreno,
moldea el mapa, crea las misiones, dirige la partida en directo y la adapta. Cada NPC decide
por su cuenta, con su carácter.

Paquetes y módulos principales:

- ``zeus``: el director de juego (campañas, misiones, decorado, sorpresas, final).
- ``agentes`` y ``mando``: los NPC (comandante, jefes de grupo, pilotos y operador de radio).
- ``ambientacion``: la época y los bandos que inventa Zeus para cada campaña.
- ``memoria``: la base de datos (BBDD/), el vigilante del espacio y el aprendizaje.
- ``archivo``: las fichas e informes de texto de la carpeta CAMPAÑAS/.
- ``simulador``: mundo de texto para jugar y probar sin ARMA 3.
- ``puente``: interfaz que implementará la conexión real con ARMA 3.
- ``llm``: clientes del modelo (Ollama y un modelo falso para pruebas).
- ``habilidades``: aprendizaje numérico de maniobras (aterrizaje de helicópteros).
"""

__version__ = "0.2.0"

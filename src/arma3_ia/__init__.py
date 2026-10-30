"""arma3-ia: núcleo de inteligencia artificial local y multiagente para ARMA 3.

Paquetes principales:

- ``llm``: clientes de modelos de lenguaje (Ollama y un LLM falso para pruebas).
- ``agentes``: comandante, líderes de escuadra, pilotos y enlace con los jugadores.
- ``comunicacion``: red de radio entre agentes.
- ``memoria``: base de datos persistente, recuperación de lecciones y reflexión post-misión.
- ``campanas``: perfiles temáticos (moderno, WWII, insurgencia...).
- ``habilidades``: aprendizaje numérico de maniobras (p. ej. aterrizaje de helicópteros).
- ``simulador``: mundo de texto para probar la IA sin ARMA 3.
- ``puente``: interfaz que implementará la conexión real con ARMA 3.
"""

__version__ = "0.1.0"

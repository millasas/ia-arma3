# BBDD: la base de datos de la IA

Aquí se guarda `ia-arma3.db`, la base de datos de la IA. Vive en el mismo PC que la IA y no se
sube a git.

Qué guarda:

- **Campañas**: lo que pediste, la ambientación, el objetivo final, los pasos, los personajes,
  el margen de error y cómo va (en curso, cumplida o fracasada).
- **Misiones**: cada misión jugada, con su resultado, su duración y su informe.
- **Lo que pasó en cada misión**: los sucesos (contactos, bajas, objetivos...), las decisiones
  de cada NPC, la radio y el chat.
- **El lore**: la historia de cada campaña, misión a misión (crónicas de Zeus, caídos,
  imprevistos y final).
- **Lo aprendido**: lecciones de los NPC y de Zeus y la práctica de vuelo. Esto se conserva
  aunque borres campañas.

Cada vez que se guarda algo, la IA revisa el espacio (`arma3-ia espacio` lo enseña). Si se
acerca al límite avisa, y si se pasa entra en modo ahorro y te recomienda borrar campañas
terminadas (`arma3-ia campana borrar <id>`). Al acabar una campaña te pregunta si quieres
borrarla para ganar espacio.

También puede aparecer `dataset.jsonl` si exportas decisiones para entrenar el modelo
(`arma3-ia exportar-dataset`).

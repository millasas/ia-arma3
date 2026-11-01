# CAMPAÑAS: tus campañas en texto

Cada campaña que pides tiene aquí su carpeta, con ficheros pequeños que se leen con cualquier
editor (la base de datos de `BBDD/` guarda lo mismo con mucho más detalle):

```
CAMPAÑAS/
  operacion_pyrgos/                 una carpeta por campaña
    campana.txt                     ficha: encargo, objetivo, pasos, personajes y cómo va
    informe_final.txt               resumen de la campaña cuando termina
    misiones/
      01_ojos_sobre_pyrgos/
        mision.yaml                 lo que preparó Zeus: terreno, decorado, grupos y sorpresas
        informe.txt                 informe corto de la misión
      02_.../
```

Si borras una campaña con `arma3-ia campana borrar <id>`, se borra también su carpeta (salvo
que añadas `--conservar-informes`). Estas carpetas no se suben a git.

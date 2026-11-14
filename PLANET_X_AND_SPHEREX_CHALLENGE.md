# NASA Space Apps Challenge 2026: Planet X and SPHEREx

> **URL Oficial del Desafío:** [https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/](https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/)  
> **Evento:** 2026 NASA Space Apps Challenge  
> **Dificultad:** Avanzado (Advanced)  
> **Categorías:** Astrofísica (Astrophysics) · Planetas y Lunas (Planets & Moons) · Software  

---

## 📋 Resumen del Desafío (Challenge Summary)

Desde 2025, la misión **SPHEREx de la NASA** ha estado mapeando la totalidad de la bóveda celeste cada seis meses en **102 bandas de luz en el infrarrojo cercano**, transmitiendo imágenes de más de mil millones de objetos astronómicos. 

Cometas, asteroides, estrellas cercanas, enanas marrones e incluso nuevos planetas (como el hipotético Planeta X / Planeta 9) se revelan a sí mismos **cambiando de posición entre imágenes tomadas en diferentes épocas**. Sin embargo, ninguna persona ni algoritmo por sí solo puede examinar semejante volumen de datos.

**Tu desafío es crear una herramienta web pública e interactiva para visualizar las imágenes del cielo de la misión SPHEREx, haciendo que sea fácil para cualquier persona observar rápidamente cómo cambian a lo largo del tiempo (análisis temporal y detección de movimiento).**

---

## 🌌 Contexto Científico y Antecedentes (Background)

Los asteroides y cometas orbitan alrededor del Sol. Las estrellas cercanas se desplazan a través del firmamento a medida que orbitan el centro de la Vía Láctea. Además, la Tierra orbita el Sol, lo que genera paralaje astronómico (desplazamientos aparentes de los objetos más cercanos a nosotros). La detección de estos movimientos no solo permite descubrir nuevos objetos astronómicos, sino también medir sus distancias exactas.

*Por ejemplo, el planeta enano Plutón fue descubierto en 1930 cuando un astrónomo comparó dos placas fotográficas del cielo tomadas en fechas distintas y observó que un diminuto punto de luz se había movido entre ambas exposiciones (la técnica del comparador de parpadeo o blink comparator).*

En la era moderna, la comunidad astronómica ha impulsado búsquedas masivas de objetos del sistema solar exterior y vecindario solar con la colaboración de la ciudadanía. Cientos de miles de personas voluntarias han utilizado técnicas de inspección visual y series temporales para descubrir asteroides, cometas, estrellas variables y enanas marrones (cuerpos gaseosos demasiado masivos para ser planetas pero sin masa suficiente para sostener fusión estelar). La mayoría de los cometas y enanas marrones recientes han sido descubiertos por voluntarios mediante plataformas como [Backyard Worlds: Planet 9](http://backyardworlds.org/).

### El Rol Único de la Misión NASA SPHEREx
Lanzada en 2025, la misión SPHEREx genera un flujo de datos sin precedentes:
- Mapea **el 100% del cielo cada 6 meses**.
- Opera en **102 longitudes de onda del infrarrojo cercano** ($0.75\,\mu\text{m}$ a $5.0\,\mu\text{m}$).
- Es extraordinariamente sensible a firmas químicas de **hidrógeno, hielo de agua ($H_2O$), dióxido de carbono ($CO_2$), monóxido de carbono ($CO$) y moléculas orgánicas**.
- Captura más de **mil millones de fuentes celestes** (enanas marrones, cuásares, cometas, asteroides, estrellas, supernovas y galaxias distantes) que a menudo se superponen o varían en brillo.

Se requieren herramientas creativas e intuitivas de código abierto para que científicos y ciudadanos puedan explorar este catálogo monumental y participar activamente en nuevos descubrimientos cósmicos.

---

## 🎯 Objetivos y Flujo de Trabajo Requerido (Core Objectives)

### 1. Funcionalidad Principal
Crear una aplicación web accesible al público para visualizar imágenes del cielo de la misión SPHEREx, permitiendo ver de manera intuitiva y rápida las variaciones y desplazamientos a través del tiempo.

### 2. Flujo de Trabajo del Usuario (Workflow)
1. El usuario introduce las **coordenadas celestes** de una región del cielo: **Ascensión Recta (RA)** y **Declinación (Dec)**.
2. La herramienta genera y muestra una **secuencia temporal de imágenes** de esa región en forma de pequeña animación / bucle de vídeo (*time-lapse* / *blink comparator*), facilitando la detección inmediata de objetos en movimiento o con variaciones de brillo.

### 3. Objetivos Astronómicos de Prueba Obligatorios (Test Targets)
La herramienta debe probarse y validarse con los siguientes objetivos:
- **Estrella de Barnard (Barnard’s Star):** La estrella con el mayor movimiento propio conocido en el cielo.
- **Epsilon Indi B:** Un par binario de enanas marrones orbitando una estrella cercana.
- **WISE 0855:** Una enana marrón ultragélida y de rápido desplazamiento:
  - $\text{RA} = 08\text{h } 55\text{m } 10.831\text{s}$ ($133.7951^\circ$)
  - $\text{Dec} = -07^\circ 14' 42.53''$ ($-7.2451^\circ$)
- **Estrellas Variables:**
  - **RW Aurigae:** Sistema estelar joven con caídas dramáticas de brillo por discos circunestelares.
  - **R Draconis:** Estrella variable gigante roja de largo periodo (tipo Mira).

---

## 💡 Consideraciones y Guías de Diseño (Potential Considerations)

1. **Actualizaciones Dinámicas (Dynamic Updates):** Los datos de SPHEREx se publican con rapidez. La arquitectura de la herramienta debe permitir incorporar con facilidad nuevos lanzamientos y épocas de datos (Data Releases).
2. **Visualización Espectral en 102 Bandas:** Diseñar formas ingeniosas para navegar por los 102 canales infrarrojos. *Por ejemplo: sumar los datos de cada uno de los 6 detectores para maximizar la relación señal/ruido (SNR) o generar espectros fotométricos al vuelo (SED) al hacer clic en un objeto.*
3. **Procesamiento Aritmético de Imágenes:** Ofrecer a los usuarios opciones para **sumar imágenes** (para detectar objetos extremadamente débiles) o **restar imágenes tomadas en diferentes épocas** (diferenciación temporal donde los objetos estáticos desaparecen y los móviles dejan una firma positiva/negativa inconfundible).
4. **Campo de Visión Personalizable (FOV):** Permitir regular el tamaño del campo visual (*Field of View*). Campos más amplios permiten rastrear objetos de movimiento rápido, mientras que campos cerrados revelan detalles sutiles.
5. **Superposición Multimisión (Cross-Mission Overlays):** Capacidad de superponer capas de datos de otros observatorios de referencia, como la misión **Gaia** de la ESA (astrometría ultraprecisa) o el catálogo infrarrojo **WISE/NEOWISE** de la NASA.
6. **Enlaces Profundos en URL (URL Deep-linking):** Permitir consultar coordenadas mediante parámetros en la URL (ej. `?ra=133.795&dec=-7.245&zoom=1.5`), facilitando incrustar la herramienta en blogs, foros científicos y plataformas educativas.
7. **Metadatos y Banderas de Calidad (Flags):** Visualizar metadatos importantes como máscaras de saturación, banderas de contaminación óptica o artefactos de difracción.
8. **Ecosistema de Software Abierto:** Aprovechar bibliotecas astronómicas como **Astropy** en Python y visores interactivos como **Aladin Sky Atlas** (desarrollado por el Centro de Datos Astronómicos de Estrasburgo - CDS).

---

## 🔗 Recursos, Conjuntos de Datos y Enlaces Oficiales

### Recursos de la NASA:
- **NASA/IPAC Infrared Science Archive (IRSA) - Misión SPHEREx:**  
  [https://irsa.ipac.caltech.edu/Missions/spherex.html](https://irsa.ipac.caltech.edu/Missions/spherex.html)  
  *Portal principal para documentación de la misión, catálogos (SPLICES, Quick Releases QR2/QR3) y acceso a archivos FITS.*
- **Cuadernos de Tutoriales Oficiales de SPHEREx (IPAC):**  
  [https://caltech-ipac.github.io/irsa-tutorials/spherex/](https://caltech-ipac.github.io/irsa-tutorials/spherex/)  
  *Jupyter Notebooks para acceder, descargar y procesar imágenes espectrales MEF de SPHEREx con Astropy y Firefly.*
- **WiseView (Inspiración y Referencia de la Comunidad):**  
  [http://byw.tools/wiseview](http://byw.tools/wiseview)  
  *Herramienta web desarrollada para examinar secuencias temporales de la misión WISE/NEOWISE utilizada por voluntarios de ciencia ciudadana.*
- **Datos de la Misión Gaia de la ESA/NASA:**  
  [https://archive.stsci.edu/missions-and-data/gaia](https://archive.stsci.edu/missions-and-data/gaia)  
  *Catálogos astrométricos de alta precisión para cotejar movimientos propios.*

### Recursos de Agencias Espaciales Socias (CSA - Agencia Espacial Canadiense):
- **NEOSSat (Observación de Asteroides, Basura Espacial y Exoplanetas):**  
  [https://www.asc-csa.gc.ca/eng/satellites/neossat/](https://www.asc-csa.gc.ca/eng/satellites/neossat/)
- **Centro Canadiense de Datos Astronómicos (CADC):**  
  [https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/](https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/)
- **Tutorial de Datos NEOSSat en Python:**  
  [https://github.com/asc-csa/NEOSSAT_Tutorial](https://github.com/asc-csa/NEOSSAT_Tutorial)
- **Conjunto de Datos de NEOSSat:**  
  [https://data.asc-csa.gc.ca/dataset/neossat](https://data.asc-csa.gc.ca/dataset/neossat)

### Otras Herramientas Comunitarias:
- **Backyard Worlds: Planet 9:** [http://backyardworlds.org/](http://backyardworlds.org/)
- **Astropy Project:** Herramientas estándar en Python para FITS, WCS y unidades astronómicas.
- **Aladin Lite / CDS:** Visor de cielo multirresolución interactivo.

---

## 🏆 Criterios de Evaluación y Entregables Clave

1. **Demostración Funcional (Working Web Application):**
   - Interfaz web accesible y pública.
   - Búsqueda directa por coordenadas celestes (RA / Dec).
   - Animación o parpadeo interactivo (*blink viewer*) entre épocas temporales de SPHEREx.
2. **Validación con los Objetivos de Prueba:**
   - Demostrar que la herramienta localiza y visualiza con claridad:
     - *Barnard's Star* (movimiento propio alto).
     - *WISE 0855* (enana marrón de movimiento rápido).
     - *Epsilon Indi B* (par binario).
     - *RW Aurigae* / *R Draconis* (variabilidad fotométrica).
3. **Manejo de las 102 Bandas Infrarrojas:**
   - Opciones para alternar canales espectrales, agrupar detectores y extraer curvas espectrales SED en tiempo real.
4. **Herramientas de Descubrimiento:**
   - Resta/diferenciación de imágenes entre épocas (*Image Subtraction*).
   - Control dinámico de FOV y contraste/estiramiento astronómico (*asinh*, log).
   - Parámetros en URL para compartir descubrimientos directamente mediante enlace web.
5. **Repositorio Público y Pitch Video:**
   - Código abierto documentado en GitHub.
   - Vídeo demostrativo explicando el impacto científico y cómo cualquier usuario puede descubrir el hipotético Planeta X o nuevos cuerpos menores del Sistema Solar.

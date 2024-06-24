from flask import Flask, render_template
from busqueda import buscar_s3_public_access  # Importar la función del script busqueda.py
import json

app = Flask(__name__)

# Página de inicio que utiliza una plantilla HTML
@app.route('/')
def home():
    return render_template('index.html')

# Manejador de errores para la página 404
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Ruta para ejecutar la búsqueda de S3 y mostrar los resultados
@app.route('/buscar_s3')
def buscar_s3():
    resultados_json = buscar_s3_public_access()
    resultados = json.loads(resultados_json)
    return render_template('resultados.html', resultados=resultados)

if __name__ == '__main__':
    app.run(host='localhost', port=3000, debug=True)

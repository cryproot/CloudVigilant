from flask import Flask, render_template
from busqueda import buscar_s3_public_access  # Importar función de busqueda.py para S3
from busqueda2 import buscar_ec2_configuraciones  # Importar función de busqueda2.py para EC2
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

# Ruta para ejecutar la búsqueda de EC2 y mostrar los resultados
@app.route('/buscar_ec2')
def buscar_ec2():
    resultados_json = buscar_ec2_configuraciones()
    resultados = json.loads(resultados_json)
    return render_template('resultadosec2.html', resultados=resultados)

if __name__ == '__main__':
    app.run(host='localhost', port=3000, debug=True)

(function(window){
  "use strict";

  function normalizar(texto){
    return String(texto || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function palabras(texto){
    return normalizar(texto)
      .split(/[^a-z0-9ñ]+/)
      .filter(Boolean);
  }

  function reducirPalabra(palabra){
    let reducida = palabra;
    const diminutivos = ["itos", "itas", "ito", "ita"];

    for(const sufijo of diminutivos){
      if(
        reducida.endsWith(sufijo) &&
        reducida.length - sufijo.length >= 4
      ){
        reducida = reducida.slice(0, -sufijo.length);
        break;
      }
    }

    if(reducida.endsWith("es") && reducida.length >= 7){
      reducida = reducida.slice(0, -2);
    }else if(reducida.endsWith("s") && reducida.length >= 6){
      reducida = reducida.slice(0, -1);
    }

    return reducida;
  }

  function longitudPrefijoComun(primera, segunda){
    const limite = Math.min(primera.length, segunda.length);
    let longitud = 0;

    while(
      longitud < limite &&
      primera[longitud] === segunda[longitud]
    ){
      longitud += 1;
    }

    return longitud;
  }

  function coincidenPalabras(consulta, candidata){
    if(consulta === candidata){
      return true;
    }

    const raizConsulta = reducirPalabra(consulta);
    const raizCandidata = reducirPalabra(candidata);
    const longitudMenor = Math.min(
      raizConsulta.length,
      raizCandidata.length
    );

    if(longitudMenor < 4){
      return false;
    }

    const prefijoComun = longitudPrefijoComun(
      raizConsulta,
      raizCandidata
    );

    return (
      prefijoComun >= 4 &&
      prefijoComun / longitudMenor >= 0.8
    );
  }

  function coincide(consulta, contenido){
    const consultaNormalizada = normalizar(consulta);
    const contenidoNormalizado = normalizar(contenido);

    if(!consultaNormalizada){
      return true;
    }

    if(contenidoNormalizado.includes(consultaNormalizada)){
      return true;
    }

    const palabrasConsulta = palabras(consultaNormalizada);
    const palabrasContenido = palabras(contenidoNormalizado);

    return palabrasConsulta.every(function(palabraConsulta){
      return palabrasContenido.some(function(palabraContenido){
        return coincidenPalabras(
          palabraConsulta,
          palabraContenido
        );
      });
    });
  }

  window.GastronomiaBusqueda = {
    coincide: coincide,
    normalizar: normalizar
  };
})(window);

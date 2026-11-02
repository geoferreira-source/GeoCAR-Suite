# GeoCAR Suite v2

Plugin QGIS para consultar imóveis do Cadastro Ambiental Rural, carregar
no projeto, cruzar com as camadas abertas e exportar.

Reescrita do zero. A v1 foi aposentada por acoplamento (um `dialog.py` de
1.400 linhas + um `.ui` monolítico) e por depender de bibliotecas
externas que quebravam dentro do Python do QGIS.

## O que mudou, e por quê

**Zero dependências externas.** Nada de geopandas, rasterio, fiona,
shapely, pandas ou openpyxl — só `requests`, que já vem no QGIS. Isso
elimina de uma vez a classe de bugs que mais custou tempo na v1:
conflito entre cópias do `proj.db` (`DATABASE.LAYOUT.VERSION` incompatível),
handshake TLS do rasterio e instalações paralelas em `AppData`.

**Área por `QgsDistanceArea` elipsoidal.** A v1 calculava a zona UTM a
partir da longitude e reprojetava. Isso produzia EPSG inexistente quando
a geometria chegava num CRS projetado, e errava em imóveis que cruzam
zonas. Agora a medida é geodésica e não depende de zona nenhuma.

**Bases de sobreposição = camadas do projeto.** Não há mais caminho de
arquivo para configurar, base por base. Você monta o projeto como já
monta; o plugin cruza com as camadas vetoriais abertas. O que resta
configurar é o inverso, e é opcional: quais camadas *ignorar*.

**Autenticação SEMAS-PA opcional.** A v1 abria o Chrome via Selenium/CDP
para capturar o Bearer token — a parte mais frágil do plugin. Agora o
token é colado pelo usuário e guardado no QSettings. E o plugin funciona
sem ele: o SICAR nacional atende todas as UFs, inclusive o Pará.

**UI em código, sem `.ui`.** Cada aba é um `QWidget` num arquivo próprio,
versionável em diff.

**Janela independente, não painel acoplado.** O plugin abre como janela
de verdade: entrada própria na barra de tarefas, minimiza sozinha, vai
para o segundo monitor e fica atrás do QGIS quando você clica no mapa.
Detalhes em `gui/janela.py`.

**Trabalho pesado em `QgsTask`.** Aparece na barra de progresso do QGIS e
é cancelável. A rede roda na thread secundária; tudo que toca o projeto
acontece no callback, na thread principal.

## Estrutura

```
geocar_suite/
├── plugin.py              registro no QGIS (ação, menu, janela)
├── core/                  lógica, sem nada de interface
│   ├── carcode.py         validação e leitura do código do CAR
│   ├── config.py          preferências (QSettings)
│   ├── cache.py           consultas guardadas (SQLite no perfil)
│   ├── cadastro.py        validação, módulo fiscal, memorial, pacote
│   ├── texto.py           número no padrão brasileiro
│   ├── layers.py          GeoJSON → camada; simbologia; camadas do projeto
│   ├── overlay.py         sobreposição + área elipsoidal
│   ├── export.py          SHP/GPKG/XLSX e leitura de planilha (OGR)
│   ├── tasks.py           QgsTask de consulta e de lote
│   └── sources/
│       ├── wfs.py         SICAR nacional (sem login)
│       ├── semas_pa.py    portal do Pará (token)
│       └── router.py      escolhe a fonte pela UF
├── icon.png               ícone (barra, menu, gerenciador, janela)
├── resources/
│   ├── emblema.png           ilustração do cabeçalho
│   ├── logo_tema_claro.png   logotipo para temas claros
│   └── logo_tema_escuro.png  variante para o tema escuro
└── gui/
    ├── janela.py          janela independente com as abas
    ├── consulta.py        consulta de um imóvel
    ├── busca.py           achar o imóvel sem ter o código
    ├── lote.py            processamento por planilha
    ├── cadastro.py        apoio ao cadastro de um imóvel novo
    └── configuracoes.py   preferências
```

## Fontes de dados

| | SICAR nacional (WFS) | SEMAS-PA |
|---|---|---|
| Cobertura | todas as UFs | só Pará |
| Login | não precisa | token colado |
| Traz responsável | não | sim |
| Busca por CPF/CNPJ | não | sim |

Na consulta por código, o roteador usa a SEMAS-PA no Pará **se** houver
token, e cai para o WFS se ela falhar por qualquer motivo — degradar é
melhor que travar.

Busca por CPF só existe no Pará. Não é limitação do plugin: o WFS
nacional publica apenas atributos do imóvel (`cod_imovel`, `num_area`,
`municipio`, `ind_status`) e nenhum campo de titular, porque dado
pessoal não vai para o serviço aberto. A plataforma federal (Meu Imóvel
Rural) é autosserviço — o titular entra com a conta gov.br e vê os
imóveis dele —, não uma API de consulta de terceiros.

Detalhe que custou caro descobrir na v1 e está preservado aqui: o portal
da SEMAS-PA compara o CPF como **string literal** e envia com pontuação
(`106.360.303-00`). Mandando só dígitos, a API responde 200 OK com lista
vazia — consulta válida cujo filtro nunca casa.

## Instalação

Copie a pasta `geocar_suite` para o diretório de plugins do seu perfil:

```
<perfil do QGIS>/python/plugins/geocar_suite
```

Depois **reinicie o QGIS por completo** — ele mantém o módulo Python do
plugin em cache na memória, então sobrescrever arquivos não basta.

Ative em *Complementos → Gerenciar e Instalar Complementos*.

## Achar o imóvel sem o código

A aba **Buscar** cobre o caso real mais comum: saber de quem é, ou onde
fica, mas não ter o código do CAR.

| | Por município | Por CPF/CNPJ |
|---|---|---|
| Cobertura | qualquer UF | só Pará |
| Login | não precisa | token da SEMAS-PA |
| Fonte | WFS nacional | portal autenticado |

Busca por titular só existe no Pará porque o serviço nacional aberto
não publica CPF nem nome de proprietário — é dado pessoal. A interface
diz isso antes de tentar, em vez de deixar descobrir pelo erro.

A busca por município avisa quando a lista foi cortada no limite: sem
isso, "1000 imóveis" pareceria o total do município quando é só o teto
da consulta.

## Identidade visual

Duas artes, em dois lugares diferentes:

- **`icon.png`** — a ilustração do imóvel com a pilha de camadas,
  recortada em quadrado, identifica o plugin em tudo que é lista do
  QGIS: barra de ferramentas, menu, gerenciador de complementos, e
  também o título e a barra de tarefas da janela.
- **`resources/emblema.png`** — a mesma ilustração, inteira, no
  cabeçalho da janela. Ela vive aqui porque é onde aparece grande o
  bastante para ser lida: a 24px na barra de ferramentas o detalho
  (casa, rio, árvores) vira uma mancha verde.
- **`resources/logo_tema_*.png`** — o logotipo horizontal fica no
  cabeçalho da janela, com a versão instalada à direita (é a primeira
  coisa perguntada quando algo não funciona).

O logotipo existe em duas versões porque a arte original é verde-escura:
sobre o tema escuro do QGIS ela praticamente desaparece. A variante
clara não é uma inversão — inverter trocaria qual palavra salta aos
olhos. A matiz é preservada e só a luminância é remapeada para uma faixa
clara, **mantendo a ordem original**: o "CAR" continua mais luminoso que
"Geo"/"Suite", como na marca sobre branco. A escolha segue o tema ativo
do QGIS em tempo de execução, então trocar de tema troca o logotipo.

## Cache de consultas

Toda consulta bem-sucedida é guardada num SQLite e reaproveitada quando o
mesmo código voltar. Validade padrão: 30 dias — o CAR muda em escala de
meses, não de dias.

O arquivo fica em `<perfil do QGIS>/geocar_suite/geocar_suite_cache.sqlite`,
**não** na pasta do plugin: a pasta do plugin é substituída inteira a cada
atualização, e o cache sumiria sem o usuário entender por quê.

Duas decisões que importam mais do que parecem:

- **Proveniência sempre visível.** Quando o resultado vem do cache, o
  resumo diz de quando ele é ("do cache, consultado há 3 dias") e aparece
  o botão **Reconsultar**, que descarta só aquele imóvel e vai à fonte.
  Dado guardado é legítimo; dado guardado apresentado como fresco, não —
  quem assina o laudo precisa saber qual dos dois está lendo.
- **Falha em silêncio.** Banco corrompido, disco cheio, sem permissão:
  todas as funções de `cache.py` devolvem "não tenho" e a consulta segue
  pela rede. Cache é otimização, nunca requisito.

No lote o ganho é o mais visível: reprocessar a mesma planilha deixa de
ser rede e passa a ser leitura local — a barra de status informa quantos
vieram do cache.

## Simbologia

A camada do imóvel entra vestida, em vez do cinza que o QGIS sorteia (e
que some num projeto com dez camadas). As escolhas não são de gosto:

- **Preenchimento a 18% de opacidade.** O imóvel é o *recorte* de
  análise, não o assunto: o que interessa é o que está embaixo dele —
  desmatamento, APP, embargo. Preenchimento sólido esconderia justamente
  a informação que motivou a consulta.
- **Contorno em duas passadas** — uma linha escura larga e uma âmbar fina
  por cima. É o *casing* da cartografia viária: a clara se lê sobre
  floresta e sombra, a escura sobre solo exposto e imagem clara. Uma cor
  só sempre perde num dos dois fundos.
- **Âmbar, não verde.** Verde sobre imagem de satélite da Amazônia
  desaparece; âmbar não ocorre em cobertura natural.

Desligável em Configurações, para quem usa estilo próprio ou um `.qml` do
projeto.

## Aba Cadastro — apoio a um CAR novo

**A aba não envia o cadastro, e não tem como enviar.** O SICAR não expõe
API de escrita; o cadastro só é aceito pelo módulo oficial. O que ela faz
é a parte que consome tempo e volta como pendência.

**Validação.** Geometria válida pelo GEOS, perímetro com uma feição só,
CRS definido, contagem de vértices, anéis internos, partes separadas — e,
o mais útil, **contenção**: quanto de cada tema está fora do perímetro, em
hectares. Cada tema é dissolvido antes de ser medido; sem isso, dois
polígonos de APP sobrepostos somariam área duas vezes e a validação
aprovaria uma Reserva Legal que não alcança o mínimo.

**Reserva Legal.** Percentual sobre a área do imóvel contra o mínimo do
regime (80 / 35 / 20%, art. 12 da Lei 12.651/2012). O regime é sugerido
pela UF e confirmado pelo usuário — o Maranhão entra parcialmente na
Amazônia Legal, e dentro dela há floresta, cerrado e campos.

**Módulo fiscal, sem tabela embutida.** O WFS publica, por imóvel, a área
e o número de módulos fiscais correspondente; a razão entre os dois *é* o
módulo fiscal do município. O plugin tira a mediana de uma amostra do
município. Em Altamira (PA) isso dá 75,00 ha a partir de 12 imóveis
independentes, com desvio de 0,03% — e cruzando com o `m_fiscal` que o
SICAR publica para um imóvel específico, bate na segunda casa. A vantagem
sobre embutir a tabela do INCRA é que o valor vem da mesma base que vai
julgar o cadastro, e não envelhece aqui dentro. O recorte de 4 MF aparece
no resumo porque é ele que decide regra de área consolidada.

**Vizinhança.** Busca os CARs já cadastrados no retângulo do perímetro e
separa quem apenas **confronta** de quem **invade**, com a área sobreposta
em hectares. Sobreposição com CAR ativo é das pendências que mais atrasam
cadastro, e custa meses descobrir depois do envio. Os vizinhos podem ser
carregados como camada para corrigir o desenho olhando para eles.

> **Armadilha do WFS:** o `'EPSG:4326'` no fim do filtro `BBOX` não é
> enfeite. Sem ele o GeoServer segue a ordem de eixos do WFS 2.0 e lê os
> números como *lat,lon* — a consulta volta com zero feições e parece, de
> forma convincente, que não há vizinho nenhum. Mesma caixa: 0 resultados
> sem o CRS, 15 com ele. O mesmo vale para `INTERSECTS`, por isso a
> interseção de verdade é calculada aqui e não no servidor.

**Pacote.** Um shapefile por tema, todos reprojetados para SIRGAS 2000
geográficas (EPSG:4674), que é o sistema do cadastro — mandar em UTM é
rejeição na importação.

**Memorial descritivo.** Vértices, azimutes e distâncias em UTM SIRGAS
2000, porque memorial trabalha com azimute e distância *planos*. A área
continua sendo a geodésica, que é a do cadastro. Sai em XLSX ou CSV, com
uma coluna de **erro de fechamento**: a soma vetorial dos lados, que num
polígono fechado tem de dar zero (num imóvel real de 17 km de perímetro,
deu 2,8 mm). Havendo vizinhos carregados, cada segmento recebe o
confrontante que encosta nele.

> **Armadilha do EPSG:** a zona UTM **não** sai de aritmética sobre o
> código. Os blocos norte e sul do SIRGAS 2000 se encavalam — `EPSG:31976`
> é a zona 22N, não a 16S — e um código "quase certo" produz coordenada
> plausível e errada. O candidato só é aceito depois de conferido pela
> descrição do CRS; não batendo, monta-se por PROJ.

### Palpite de camada por tema

A aba tenta adivinhar qual camada do projeto é cada tema pelo nome, e as
pistas são ordenadas **da mais específica para a mais genérica**. Isso não
é detalhe: num projeto real, `"Limite de processamento"` era escolhido
como perímetro do imóvel só por conter "limite", enquanto a camada certa
estava ali do lado. Palpite errado que parece certo é pior que palpite
nenhum.

## Compatibilidade declarada

`metadata.txt` declara `qgisMinimumVersion=3.28` **e**
`qgisMaximumVersion=4.99`. O máximo não é opcional: sem ele o QGIS assume
o teto `<major>.99` do mínimo declarado — com mínimo 3.x isso vira 3.99,
e o QGIS 4.x recusa o plugin com "complemento incompatível com essa
versão do QGIS", mesmo com o código funcionando perfeitamente.

## Testado em

QGIS 4.2 (Qt6, Python 3.12), com consulta real ao SICAR nacional.

Os idiomas do QGIS 3.x usados aqui (`QVariant` em `QgsField`,
`QgsWkbTypes.*Geometry`, `QgsTask.CanCancel`, `QgsUnitTypes.AreaSquareMeters`)
continuam valendo por compatibilidade no 4.2 — o QGIS mantém os próprios
enums. Os enums do **Qt**, porém, ficaram estritos no PyQt6: `Qt.Checked`
e companhia não existem mais, então a interface usa sempre a forma
escopada (`Qt.CheckState.Checked`), que o PyQt5 recente também aceita.

### Três bugs encontrados testando ao vivo

1. **Nomes de campo do WFS errados.** A v1 lia `num_area` e `ind_status`,
   que não existem na camada — os nomes certos são `area` e
   `status_imovel`. Por isso área e situação vinham vazias. Ironicamente a
   documentação do projeto trazia os nomes certos; o código, não. Agora os
   nomes ficam em constantes no topo de `wfs.py`.

2. **Área medida no CRS errado.** O medidor usava o CRS do *projeto*, não
   o da geometria. Como o QGIS adota o CRS da primeira camada adicionada,
   a mesma medição dava resultado diferente antes e depois de a camada
   entrar no mapa — e dava **zero** com o projeto em Web Mercator. O
   perigoso: área do imóvel e área sobreposta erravam pelo mesmo fator,
   então o *percentual* saía correto e a falha passava despercebida.

3. **Enums do Qt desescopados**, que quebravam a interface inteira no
   PyQt6.

4. **Número no formato errado.** O idioma `f"{v:,.2f}".replace(",", ".")`
   estava espalhado pela v2: a troca é cega e atinge tudo. `1234.5`
   virava `"1.234.50"` — milhar e decimal com o mesmo símbolo — e, quando
   a frase inteira passava pelo `replace`, a vírgula do **texto** virava
   ponto ("5,30% do imóvel**.** abaixo dos 80%"). Abaixo de mil hectares
   ninguém percebe; acima, o número fica ambíguo justamente nos imóveis
   grandes. Agora há `core/texto.py`, e a troca é em dois passos com
   marcador.

Validação final num imóvel real de Altamira (PA): 886,74 ha medidos contra
887,61 ha declarados pelo SICAR — 0,1% de diferença, o esperado entre
medida geodésica e a do cadastro.

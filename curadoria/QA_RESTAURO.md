# QA visual — primeira restauração de modelos do ArtiSys Oficina

> **Status: experimental / não homologado.** Capturas realizadas dos GLBs reais. O processo corrige materiais e pintura, **não** recompõe geometria ausente nem certifica correspondência de geração/ano.

## Revisão das cinco variantes

| Modelo | Resultado de materiais | Rodas / geometria | Próximo passo |
|---|---|---|---|
| Fiat Uno | Pintura azul petróleo, carroceria distinguível e pneus visíveis | Rodas presentes no render | Revisar fidelidade visual e vinculação das peças |
| Chevrolet Corsa Sedan | Deixou de ficar branco; carroceria azul acinzentada, rodas visíveis | Geometria básica presente | Separar vidros e detalhes que continuam com materiais genéricos |
| Ford Ka | Pintura e vidros mais naturais; pneus recebem material escuro | **Ainda há vãos de roda incompletos no render** | Inspecionar posicionamento real das malhas `fmka_wheels` e `fmka_tires`, restaurar em Blender se necessário |
| Volkswagen Fox | Pintura azul petróleo e vidros menos saturados | **Ainda há vãos de roda incompletos no render** | Inspecionar `fmfox_wheels` e `fmfox_tires`, recuperar geometria/transformações |
| Fiat Palio | Verde neon substituído por pintura neutra | **Rodas ausentes na prévia** | Confirmar ausência no GLB e modelar/adaptar rodas de proporções corretas, marcando eventual geometria criada |

## Comparativo real

| Modelo | Original | Restaurado |
|---|---|---|
| Fiat Uno | ![Uno original](../miniaturas/fiat-uno-frente.png) | ![Uno restaurado](../miniaturas-restauradas/fiat-uno-frente.png) |
| Fiat Palio | ![Palio original](../miniaturas/fiat-palio-frente.png) | ![Palio restaurado](../miniaturas-restauradas/fiat-palio-frente.png) |
| Chevrolet Corsa | ![Corsa original](../miniaturas/chevrolet-corsa-frente.png) | ![Corsa restaurado](../miniaturas-restauradas/chevrolet-corsa-frente.png) |
| Ford Ka | ![Ka original](../miniaturas/ford-ka-frente.png) | ![Ka restaurado](../miniaturas-restauradas/ford-ka-frente.png) |
| Volkswagen Fox | ![Fox original](../miniaturas/vw-fox-frente.png) | ![Fox restaurado](../miniaturas-restauradas/vw-fox-frente.png) |

## Alterar pintura gratuitamente

```bash
python3 scripts/curadoria/restaurar_glb.py --ids ford-ka --paint '#285F83' --output curadoria/pintura-azul
python3 scripts/curadoria/restaurar_glb.py --ids vw-fox --paint '#8E263E' --output curadoria/pintura-vinho
```

Os arquivos `assets/*.glb` **não são alterados**. As variantes derivadas ficam no diretório escolhido, com SHA-256 e créditos dos criadores registrados no `manifest.json`. Para disponibilizar alteração de cor dentro do Oficina, o ideal é aplicar o perfil de materiais no visualizador Three.js, sem distribuir um GLB duplicado para cada cor.

## Critério de liberação para o Oficina

1. Identificação exata de carroceria, geração e modelo do veículo
2. Rodas, pneus e partes externas visualmente completas
3. Texturas e materiais coerentes, sem vidros opacos ou pintura estourada
4. Peças clicáveis somente quando malhas separadas forem verificadas
5. Licenças e atribuições originais mantidas; revisões registradas
6. Aprovação visual humana explícita antes da instalação pelo usuário

**Não publicar esses cinco modelos como homologados na versão atual.**

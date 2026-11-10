# RAG Local — Paquete de rediseño para Codex

1. Descomprime este paquete en la raíz del repositorio de RAG Local (o en una carpeta `docs/redesign/`).
2. Entrega a Codex el contenido de **`PROMPT.md`** como instrucción, indicándole la ruta del paquete.
3. Abre `mockups/*.html` en el navegador para ver el objetivo visual (funcionan sin conexión).

```
rag-local-redesign/
├── PROMPT.md                 ← instrucción principal para el agente
├── spec/comportamiento.md    ← estados, textos, modales, accesibilidad y seguridad
├── design/
│   ├── tokens.css            ← paleta "Lino y arcilla", tipografía, radios, sombras
│   ├── fonts.css             ← @font-face autoalojadas
│   └── fonts/                ← Bricolage Grotesque, Instrument Sans, JetBrains Mono (OFL 1.1)
├── mockups/                  ← consulta, biblioteca, modal de éxito, modal de error
└── referencia-actual/        ← capturas de la interfaz actual
```

Paleta principal: lino `#F3EEE5`, arcilla `#A84B2A`, musgo `#4E6A36`, ocre `#C08A2E`, ladrillo `#9B2F26`, oliva profundo `#2B3025`.

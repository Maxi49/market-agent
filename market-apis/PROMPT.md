# Guía de Prompt Engineering para Agentes

Research consolidado de Anthropic, OpenAI y literatura académica (2024-2025).
Aplicado al prompt de `marketShoppingAgent`.

---

## Principios fundamentales

### 1. Las definiciones de tools SON prompt engineering

Anthropic dice explícitamente que gastaron más tiempo optimizando los tools que el system prompt en su agente SWE-bench. Una tool bien definida tiene:

- Qué hace
- **Cuándo usarla**
- **Cuándo NO usarla** — esto es lo más ignorado y lo más impactante
- Qué devuelve (y qué no devuelve)

**Malo:**
```
"Gets the stock price for a ticker."
```

**Bueno:**
```
"Retrieves the current stock price for a given ticker symbol. Only use when the user asks about the current price of a specific stock. It will NOT provide historical data or company information."
```

**Regla de nombrado:** usar `github_list_prs` en vez de `list_prs`. Prefijos de servicio eliminan ambigüedad cuando hay muchos tools. Usar `absolute_path` en vez de `path` — el nombre del parámetro es instrucción implícita.

**Consolidar tools similares:** en vez de `create_pr`, `review_pr`, `merge_pr` → un tool con parámetro `action`. Menos tools = menos errores de selección. Un estudio redujo errores en 86.4% filtrando 31 tools a 3 semánticamente relevantes.

---

### 2. Estructura con secciones claras — el "lost in the middle" problem

Los modelos distribuyen atención de forma desigual:
1. Contenido del mensaje del usuario (máxima atención)
2. Inicio del system prompt
3. Final del system prompt
4. **Medio del system prompt** (mínima atención)

**Consecuencia:** reglas críticas enterradas en un bloque de texto largo se ignoran. Usar headers (`##`) o XML tags para delimitar secciones. Repetir las reglas más importantes al principio **y** al final.

Estructura recomendada por OpenAI (instrucciones posteriores tienen mayor prioridad en caso de conflicto):

```
## Rol y objetivo
## Reglas de uso de herramientas
## Herramientas disponibles (con cuándo sí / cuándo no)
## Comportamiento multi-paso
## Formato de respuesta
## Recordatorios críticos   ← las más importantes van acá también
```

---

### 3. Llamadas en paralelo — XML block oficial de Anthropic

Para Claude y modelos compatibles, este bloque en el system prompt eleva significativamente el uso de llamadas paralelas:

```xml
<use_parallel_tool_calls>
For maximum efficiency, whenever you perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially. Prioritize calling tools in parallel whenever possible. Err on the side of maximizing parallel tool calls rather than running too many tools sequentially.
</use_parallel_tool_calls>
```

**Trampa crítica:** si los resultados de tool calls paralelas se devuelven en mensajes de usuario separados (en vez de en un solo mensaje), el modelo aprende a dejar de llamar en paralelo. Todos los resultados paralelos deben llegar en un único mensaje.

---

### 4. Las tres instrucciones que todo agente necesita (OpenAI)

Derivadas del GPT-4.1 Prompting Guide como componentes obligatorios:

**Persistencia** — evita que el agente pare antes de terminar:
```
Keep going until the user's query is completely resolved before yielding back to the user.
```

**Enforcement de tool use** — evita respuestas inventadas:
```
If you are not sure about something, use your tools to gather the relevant information: do NOT guess or make up an answer.
```

**No-promises** — evita que el modelo diga "voy a buscar" y después responda desde memoria:
```
Do NOT promise to call a function later. If a function call is required, emit it now.
```

---

### 5. Grounding — solo datos de las herramientas

El problema: modelos completan su respuesta con información de entrenamiento aunque se les diga que usen tools. Soluciones en orden de efectividad:

**Restricción explícita de conocimiento externo:**
```
Only use information from the retrieved data. If you don't have the information needed to answer, say "No tengo suficientes datos para responder esto con certeza."
```

**Paso de extracción separado antes de síntesis:**
```
1. Extraé las citas exactas del resultado de las herramientas relevantes para la pregunta.
2. Respondé usando únicamente esas citas como base.
```

**Auditoría post-generación:**
```
Después de redactar tu respuesta, revisá cada afirmación. Para cada una, encontrá una cita directa en los resultados que la respalde. Si no encontrás soporte, eliminá la afirmación.
```

**Permiso explícito de incertidumbre** — los modelos por defecto "completan" la respuesta. Romper ese default:
```
Si no tenés datos suficientes, decilo directamente: "No encontré resultados para comparar."
```

---

### 6. El "think" tool para entornos con muchas reglas

Para agentes con muchas restricciones o pasos donde los errores son costosos, agregar un tool `think` (sin side effects) mejora la tasa de seguimiento de reglas:

```json
{
  "name": "think",
  "description": "Usalo para razonar antes de actuar. No obtiene datos nuevos ni modifica nada.",
  "input_schema": {
    "type": "object",
    "properties": {
      "thought": { "type": "string" }
    },
    "required": ["thought"]
  }
}
```

Instrucción en el system prompt:
```
Antes de cada tool call, usá 'think' para:
- Listar qué reglas aplican al pedido actual
- Verificar que tenés toda la información necesaria
- Confirmar que la acción planificada cumple con las restricciones
```

Útil cuando: el output de tools requiere procesamiento cuidadoso, hay políticas complejas, o errores en un paso se propagan al siguiente.

---

### 7. Few-shot examples — "una imagen vale mil palabras"

Anthropic recomienda 2-5 ejemplos canónicos y diversos, no una enumeración exhaustiva de edge cases. Para tools con inputs complejos o sensibles al formato, usar `input_examples` en la definición:

```json
"input_examples": [
  {"location": "San Francisco, CA", "unit": "fahrenheit"},
  {"location": "Buenos Aires"}
]
```

Costo aproximado: 20-50 tokens por ejemplo simple. Vale la pena para cualquier tool con formato de input ambiguo.

---

### 8. Mensajes de error de tools — accionables, no opacos

Los errores que devuelven los tools deben incluir qué hacer a continuación, no solo qué falló:

**Malo:** `"Error 404"`

**Bueno:** `"Producto no encontrado con ese ID. Intentá buscar por nombre usando search-products con una query más general."`

Si el output fue truncado, explicarlo:
```
"Output de 5000 tokens truncado a 1000. Para ver el detalle completo, llamá get-details con el ID específico."
```

---

## Anti-patterns documentados

| Pattern | Problema | Solución |
|---------|----------|----------|
| Regla importante solo en el medio del prompt | Se ignora (lost in the middle) | Repetir al inicio y al final |
| "Usá el tool X cuando necesites Y" sin casos de no-uso | El modelo lo llama en situaciones incorrectas | Agregar "NO usarlo cuando..." |
| Múltiples tools con scope similar sin disambiguación | Selección incorrecta | Consolidar o agregar regla explícita de cuándo usar cuál |
| Instrucción implícita de grounding ("sé preciso") | Modelo igual completa con memoria | Restricción explícita + paso de extracción separado |
| Resultados de parallel calls en mensajes separados | El modelo deja de hacer parallel calls en turnos siguientes | Todos los resultados en un único mensaje |
| Tool descriptions de una línea | Alta tasa de selección incorrecta y mal uso de parámetros | Mínimo 3-4 oraciones por tool |
| Sistema de prompts sin secciones | Atención distribuida uniformemente, reglas críticas perdidas | Headers o XML tags para delimitar |

---

## Fuentes

- [Anthropic — Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
- [Anthropic — Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic — The "Think" Tool](https://www.anthropic.com/engineering/claude-think-tool)
- [Anthropic Docs — Parallel Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)
- [Anthropic Docs — Reduce Hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)
- [OpenAI Cookbook — GPT-4.1 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide)
- [OpenAI Cookbook — o3/o4-mini Function Calling Guide](https://developers.openai.com/cookbook/examples/o-series/o3o4-mini_prompting_guide)
- [Augment Code — 11 Prompting Techniques for AI Agents](https://www.augmentcode.com/blog/how-to-build-your-agent-11-prompting-techniques-for-better-ai-agents)
- [arXiv — Chain of Evidences (2401.05787)](https://arxiv.org/html/2401.05787v2)
- [AWS — Stop AI Agent Hallucinations](https://dev.to/aws/stop-ai-agent-hallucinations-4-essential-techniques-2i94)
- [ReAct — Synergizing Reasoning and Acting](https://react-lm.github.io/)

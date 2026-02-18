Microsoft Agent Framework — Architecture & Multi-Agent Research Summary
Repo & Status
GitHub: github.com/microsoft/agent-framework
PyPI: pip install agent-framework --pre — the --pre flag confirms Python SDK is in preview
Languages: Python + C#/.NET, consistent APIs across both
Docs: learn.microsoft.com/agent-framework/
Python SDK Structure
Monorepo under python/packages/ with 22 packages using namespace packaging (agent_framework.*):

Layer	Packages
Core	core — all abstractions, types, OpenAI/Azure OpenAI built-in
LLM Providers	anthropic, bedrock, claude, ollama, foundry_local
Azure	azure-ai (Foundry agents), azure-ai-search (RAG), azurefunctions (hosting)
Protocols	a2a (Agent-to-Agent), ag-ui (AG-UI protocol)
Orchestrations	orchestrations — high-level multi-agent builders
Memory/Storage	mem0, redis
Infra	copilotstudio, declarative (YAML/JSON agents), durabletask, github_copilot, purview
Experimental	lab (benchmarking, RL, research)
DevUI	devui — interactive testing/debugging UI
Import design is tiered:

Tier 0 (core): from agent_framework import Agent, tool, Workflow
Tier 1 (advanced): from agent_framework.orchestrations import HandoffBuilder
Tier 2 (providers): from agent_framework.azure import AzureOpenAIChatClient
Agent Definition Model
Key classes in _agents.py:

SupportsAgentRun — Protocol (structural subtyping). Any class with run(), create_session(), get_session(), id, name, description qualifies. No framework inheritance required.

BaseAgent — Abstract base providing session management, context providers, serialization.

Agent — Main concrete class wrapping a ChatClient + tools + instructions + middleware:

Agent(client=OpenAIChatClient(), instructions="...", tools=[my_fn], name="MyAgent")
Or shorthand: client.as_agent(name="X", instructions="...")

Lifecycle: agent.run(messages, session=session) → middleware pipeline → chat client get_response() → function invocation loop → AgentResponse

Sessions: AgentSession manages conversation state; pluggable BaseHistoryProvider (in-memory default) and BaseContextProvider (RAG, memory).

Tools / Functions
Defined in _tools.py (~2228 lines):

@tool decorator — converts any Python function to a FunctionTool with auto-generated JSON schema from type annotations
FunctionTool — wraps callables with name, description, parameters schema
ToolProtocol — protocol for custom tool implementations
use_function_invocation() — decorator adding automatic function-calling loop to chat clients
MCP support (_mcp.py) — full Model Context Protocol client (stdio, HTTP, WebSocket transports), tool approval modes
Approval modes: "always_require", "never_require", per-tool specific approval
ADR 0002-agent-tools documents the hybrid approach: generic tool abstractions + provider-specific tools + raw fallback
Multi-Agent Orchestration
Two layers:

1. Workflow Engine (core)
Graph-based DAG execution in _workflows/:

Workflow — immutable graph of Executor nodes connected by typed edges
WorkflowBuilder — fluent API: add_edge(), fan_out(), fan_in(), switch_case()
Executor — base processing unit with @handler methods that receive typed messages via WorkflowContext
AgentExecutor — wraps any SupportsAgentRun agent as a workflow node
FunctionExecutor / @executor — wraps plain functions
Features: streaming, checkpointing (FileCheckpointStorage, InMemoryCheckpointStorage), human-in-the-loop, type-safe edges, visualization
2. Orchestration Builders (orchestrations package)
High-level patterns in orchestrations/:

Pattern	Class	Description
Sequential	SequentialBuilder	Chain agents, passing conversation context along
Concurrent	ConcurrentBuilder	Fan-out to agents in parallel, then aggregate
Handoff	HandoffBuilder	Decentralized routing — agents decide handoff targets via tool calls
Group Chat	GroupChatBuilder	Centralized orchestrator-directed multi-agent conversations
Magentic	MagenticBuilder	Magentic-One pattern — manager agent coordinates specialists with progress ledger
Handoff is particularly relevant: agents get auto-registered handoff tools, maintain shared conversation, support both autonomous and human-in-the-loop modes.

Magentic uses a manager agent + specialists (researcher, coder, etc.) with structured planning.

LLM Integration
Built-in (in core):

OpenAIChatClient, OpenAIResponsesClient
AzureOpenAIChatClient, AzureOpenAIResponsesClient
Pluggable (separate packages):

Anthropic, Bedrock, Claude Agent SDK, Ollama, Foundry Local
Any custom client implementing BaseChatClient._inner_get_response() works
Protocol: SupportsChatGetResponse — implement this to add any LLM backend.

Middleware System
Three interception points (ADR 0007):

AgentMiddleware — intercepts agent.run() calls
ChatMiddleware — intercepts client.get_response() calls
FunctionMiddleware — intercepts tool/function invocations
Pipeline pattern with call_next(). Used for logging, guardrails, telemetry, approval workflows.

Observability
Built-in OpenTelemetry integration — distributed tracing for agents, workflows, and function calls. Span creation helpers, metric histograms for function invocation duration.

Implications for a KG Construction Pipeline
If building a knowledge graph construction pipeline with this framework:

Agent-per-stage: Define specialized agents (entity extraction, relation extraction, entity resolution, graph construction) each wrapping an LLM client with domain-specific instructions and tools
Sequential or DAG workflow: Use WorkflowBuilder for a typed pipeline: raw text → entity extraction → relation extraction → entity resolution → graph upsert, or SequentialBuilder for simpler chaining
Tools for graph operations: Use @tool to wrap Neo4j/graph DB operations (create nodes, merge edges, query for dedup) — agents can call these during their reasoning
Concurrent fan-out: Use ConcurrentBuilder or fan_out edges to process multiple documents in parallel
Checkpointing: Built-in checkpoint storage enables resumable pipelines over large corpora
Custom executors: For non-LLM steps (embedding, clustering), use @executor or FunctionExecutor directly — no agent overhead
MCP integration: Could expose graph DB as an MCP server for agents to query
Session/state management: AgentSession.state can carry accumulated graph state across pipeline stages
Middleware: Add validation middleware to verify extracted entities/relations before graph insertion
The framework is well-suited — it separates the LLM orchestration concern from the graph construction logic, supports typed message passing between stages, and provides the plumbing (streaming, checkpointing, observability) that a production pipeline needs.
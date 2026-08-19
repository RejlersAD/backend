"""
Base Agent Class
Soft-coded AI agent with configuration-driven behavior
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Base class for AI agents with soft-coded configuration
    All agent behavior is driven by configuration, not hardcoded
    """
    
    # Agent configuration (can be overridden or loaded from DB)
    AGENT_CONFIG = {
        'name': 'Base Agent',
        'description': 'Base AI agent',
        'model': 'gpt-4o',
        'temperature': 0.1,
        'max_tokens': 2000,
        'system_prompt': '',
        'capabilities': [],
        'tools': []
    }
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize agent with optional custom configuration
        
        Args:
            config: Custom configuration to override defaults
        """
        # Merge custom config with default
        self.config = {**self.AGENT_CONFIG, **(config or {})}
        
        # Initialize OpenAI client
        self.client = None
        if OpenAI:
            api_key = os.getenv('OPENAI_API_KEY')
            if api_key:
                self.client = OpenAI(api_key=api_key)
        
        # Agent state
        self.conversation_history = []
        
        logger.info(f"Initialized {self.config['name']}")
    
    @abstractmethod
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent task (must be implemented by subclass)
        
        Args:
            task: Task parameters
            
        Returns:
            Task results
        """
        pass
    
    def call_llm(self, prompt: str, context: Optional[Dict] = None,
                 temperature: Optional[float] = None) -> str:
        """
        Call LLM with prompt and context
        
        Args:
            prompt: User prompt
            context: Additional context data
            temperature: Override default temperature
            
        Returns:
            LLM response text
        """
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")
        
        # Build messages
        messages = [
            {"role": "system", "content": self.config['system_prompt']}
        ]
        
        # Add conversation history if enabled
        if self.config.get('use_history', False):
            messages.extend(self.conversation_history)
        
        # Add context if provided
        if context:
            context_text = self._format_context(context)
            messages.append({"role": "system", "content": f"Context:\n{context_text}"})
        
        # Add user prompt
        messages.append({"role": "user", "content": prompt})
        
        # Call API
        try:
            response = self.client.chat.completions.create(
                model=self.config['model'],
                messages=messages,
                temperature=temperature or self.config['temperature'],
                max_tokens=self.config['max_tokens']
            )
            
            result = response.choices[0].message.content
            
            # Update history
            if self.config.get('use_history', False):
                self.conversation_history.append({"role": "user", "content": prompt})
                self.conversation_history.append({"role": "assistant", "content": result})
            
            return result
            
        except Exception as e:
            logger.error(f"LLM call failed: {str(e)}")
            raise
    
    def call_llm_structured(self, prompt: str, schema: Dict,
                           context: Optional[Dict] = None,
                           temperature: Optional[float] = None) -> Dict:
        """
        Call LLM and parse response as structured JSON

        Args:
            prompt: User prompt
            schema: Expected JSON schema
            context: Additional context
            temperature: Override default temperature

        Returns:
            Parsed JSON response
        """
        # Add JSON instruction to prompt
        json_prompt = f"""{prompt}

Return your response as a JSON object matching this schema:
{json.dumps(schema, indent=2)}

Return ONLY the JSON object, no additional text."""

        response_text = self.call_llm(json_prompt, context, temperature=temperature)
        
        # Extract and parse JSON
        try:
            # Try to find JSON in response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            
            if start_idx >= 0 and end_idx > start_idx:
                json_text = response_text[start_idx:end_idx]
                return json.loads(json_text)
            else:
                # Try parsing entire response
                return json.loads(response_text)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            logger.debug(f"Response text: {response_text}")
            raise ValueError(f"Invalid JSON response from LLM: {str(e)}")
    
    def _format_context(self, context: Dict) -> str:
        """Format context dictionary as readable text"""
        lines = []
        for key, value in context.items():
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for k, v in value.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
    
    def reset_history(self):
        """Clear conversation history"""
        self.conversation_history = []
    
    def update_config(self, config_updates: Dict):
        """
        Update agent configuration dynamically
        
        Args:
            config_updates: Configuration parameters to update
        """
        self.config.update(config_updates)
        logger.info(f"Updated config for {self.config['name']}")
    
    def get_capabilities(self) -> List[str]:
        """Get list of agent capabilities"""
        return self.config.get('capabilities', [])
    
    def log_execution(self, task: Dict, result: Dict):
        """Log agent execution for audit trail"""
        logger.info(f"Agent: {self.config['name']}")
        logger.info(f"Task: {task.get('type', 'unknown')}")
        logger.info(f"Success: {result.get('success', False)}")
        
        if result.get('error'):
            logger.error(f"Error: {result['error']}")


class AgentOrchestrator:
    """
    Orchestrates multiple agents for complex workflows
    Soft-coded workflow definitions
    """
    
    def __init__(self):
        """Initialize orchestrator"""
        self.agents = {}
        self.workflows = {}
    
    def register_agent(self, agent_id: str, agent: BaseAgent):
        """
        Register an agent
        
        Args:
            agent_id: Unique agent identifier
            agent: Agent instance
        """
        self.agents[agent_id] = agent
        logger.info(f"Registered agent: {agent_id}")
    
    def register_workflow(self, workflow_id: str, workflow_config: Dict):
        """
        Register a workflow with soft-coded steps
        
        Args:
            workflow_id: Unique workflow identifier
            workflow_config: Workflow configuration
            
        Example workflow config:
        {
            'name': 'PDF Extraction Workflow',
            'steps': [
                {'agent': 'document_analyzer', 'action': 'analyze'},
                {'agent': 'field_extractor', 'action': 'extract'},
                {'agent': 'validation_agent', 'action': 'validate'}
            ],
            'on_error': 'continue'  # or 'stop'
        }
        """
        self.workflows[workflow_id] = workflow_config
        logger.info(f"Registered workflow: {workflow_id}")
    
    def execute_workflow(self, workflow_id: str, initial_data: Dict) -> Dict:
        """
        Execute a workflow
        
        Args:
            workflow_id: Workflow to execute
            initial_data: Initial workflow data
            
        Returns:
            Workflow results
        """
        if workflow_id not in self.workflows:
            raise ValueError(f"Workflow not found: {workflow_id}")
        
        workflow = self.workflows[workflow_id]
        logger.info(f"Executing workflow: {workflow['name']}")
        
        results = {
            'workflow_id': workflow_id,
            'success': True,
            'steps': [],
            'data': initial_data
        }
        
        # Execute each step
        for step_idx, step in enumerate(workflow['steps']):
            agent_id = step['agent']
            action = step['action']
            
            if agent_id not in self.agents:
                error = f"Agent not found: {agent_id}"
                logger.error(error)
                
                if workflow.get('on_error') == 'stop':
                    results['success'] = False
                    results['error'] = error
                    return results
                else:
                    results['steps'].append({
                        'step': step_idx,
                        'agent': agent_id,
                        'action': action,
                        'success': False,
                        'error': error
                    })
                    continue
            
            # Execute step
            try:
                agent = self.agents[agent_id]
                
                task = {
                    'type': action,
                    'data': results['data'],
                    'step_config': step.get('config', {})
                }
                
                step_result = agent.execute(task)
                
                # Update workflow data
                if step_result.get('success'):
                    results['data'].update(step_result.get('data', {}))
                
                results['steps'].append({
                    'step': step_idx,
                    'agent': agent_id,
                    'action': action,
                    'success': step_result.get('success', False),
                    'result': step_result
                })
                
                # Check if should stop on error
                if not step_result.get('success') and workflow.get('on_error') == 'stop':
                    results['success'] = False
                    results['error'] = step_result.get('error', 'Step failed')
                    return results
                    
            except Exception as e:
                error = f"Step execution failed: {str(e)}"
                logger.error(error)
                
                results['steps'].append({
                    'step': step_idx,
                    'agent': agent_id,
                    'action': action,
                    'success': False,
                    'error': error
                })
                
                if workflow.get('on_error') == 'stop':
                    results['success'] = False
                    results['error'] = error
                    return results
        
        logger.info(f"Workflow {workflow_id} completed")
        return results
    
    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Get registered agent by ID"""
        return self.agents.get(agent_id)
    
    def list_workflows(self) -> List[Dict]:
        """List all registered workflows"""
        return [
            {
                'id': wf_id,
                'name': wf_config['name'],
                'steps': len(wf_config['steps'])
            }
            for wf_id, wf_config in self.workflows.items()
        ]

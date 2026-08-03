"""
Agent Orchestrator - Coordinates all agents
"""
from typing import Dict, List, Any
from .verifier_agent import VerifierAgent
from .sanity_checker_agent import SanityCheckerAgent
from .formatter_agent import FormatterAgent


class AgentOrchestrator:
    """
    Orchestrates the 3-agent pipeline:
    1. Verifier - Find missed issues
    2. Sanity Checker - Remove hallucinations
    3. Formatter - Standardize output
    """
    
    def __init__(self):
        self.verifier = VerifierAgent()
        self.sanity_checker = SanityCheckerAgent()
        self.formatter = FormatterAgent()
        
        print("[AGENT ORCHESTRATOR] Initialized 3-agent pipeline")
    
    def run(
        self,
        extracted_data: Dict[str, Any],
        rule_issues: List[Dict[str, Any]],
        images_base64: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Run full agentic validation pipeline
        
        Args:
            extracted_data: Ground truth from extractor
            rule_issues: Issues from deterministic rules
            images_base64: P&ID images
        
        Returns:
            Final validated and formatted issues
        """
        print("[AGENT ORCHESTRATOR] Starting 3-agent pipeline...")
        
        # AGENT 1: Verifier - Find additional issues
        print("[AGENT ORCHESTRATOR] Step 1: Verification")
        additional_issues = self.verifier.verify(
            extracted_data,
            rule_issues,
            images_base64
        )
        
        # Combine rule issues + verifier issues
        all_issues = rule_issues + additional_issues
        print(f"[AGENT ORCHESTRATOR] Combined: {len(all_issues)} total issue(s)")
        
        # AGENT 2: Sanity Checker - Remove hallucinations
        print("[AGENT ORCHESTRATOR] Step 2: Sanity Check")
        valid_issues = self.sanity_checker.check(all_issues, extracted_data)
        
        # AGENT 3: Formatter - Standardize output
        print("[AGENT ORCHESTRATOR] Step 3: Formatting")
        formatted_issues = self.formatter.format(valid_issues)
        
        print(f"[AGENT ORCHESTRATOR] Final output: {len(formatted_issues)} issue(s)")
        return formatted_issues

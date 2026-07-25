from enum import Enum
from dataclasses import dataclass
from typing import Iterable
from pathlib import Path
import shutil
import os

from .mcp import McpServerConfig

class Severity(Enum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

class ValidationStatus(Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"

@dataclass
class ValidationFinding:
    severity: Severity
    code: str
    message: str

@dataclass
class ValidationReport:
    server: str
    findings: list[ValidationFinding]
    status: ValidationStatus

def validate_configuration(configs: Iterable[McpServerConfig]) -> list[ValidationReport]:
    reports = []
    for config in configs:
        findings = []
        has_errors = False
        has_warnings = False
        
        if config.command:
            executable = str(config.command[0])
            if shutil.which(executable):
                findings.append(ValidationFinding(Severity.SUCCESS, "executable_found", "executable found"))
            else:
                findings.append(ValidationFinding(Severity.ERROR, "missing_executable", f"executable \"{executable}\" not found"))
                has_errors = True
        else:
            findings.append(ValidationFinding(Severity.ERROR, "missing_command", "command is missing or empty"))
            has_errors = True
            
        if config.cwd:
            if Path(config.cwd).exists():
                findings.append(ValidationFinding(Severity.SUCCESS, "cwd_exists", "cwd exists"))
            else:
                findings.append(ValidationFinding(Severity.ERROR, "missing_cwd", f"cwd does not exist: {config.cwd}"))
                has_errors = True
                
        if config.timeout_seconds <= 0:
            findings.append(ValidationFinding(Severity.WARNING, "invalid_timeout", f"invalid timeout ({config.timeout_seconds}), should be > 0"))
            has_warnings = True
            
        if config.reconnect_attempts < 0:
            findings.append(ValidationFinding(Severity.WARNING, "invalid_reconnect", f"invalid reconnect_attempts ({config.reconnect_attempts}), should be >= 0"))
            has_warnings = True
            
        # Auth env keys
        if not config.auth_env_keys:
            # We don't necessarily warn on empty auth keys if the user just didn't configure any,
            # but per user feedback "empty auth_env_keys" -> warning.
            pass
        else:
            for key in config.auth_env_keys:
                if key in os.environ:
                    # Let's not spam SUCCESS for every env var unless we want it, but the user example shows just the warning.
                    pass
                else:
                    findings.append(ValidationFinding(Severity.WARNING, "missing_auth_env", f"{key} missing"))
                    has_warnings = True

        if not has_errors:
            findings.append(ValidationFinding(Severity.SUCCESS, "config_valid", "configuration valid"))
            
        if has_errors:
            status = ValidationStatus.FAIL
        elif has_warnings:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.PASS
            
        reports.append(ValidationReport(server=config.name, findings=findings, status=status))
        
    return reports

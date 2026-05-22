# System Architecture

## Overview

Karbot Rage! is a sophisticated automated trading system for prediction markets, designed to operate as an intelligent agent that makes informed trading decisions based on market analysis and risk management.

## Architecture Layers

### 1. Core Components
- **Configuration System**: Centralized configuration management with validation
- **Event System**: Asynchronous event handling for system communications
- **Data Handling**: Data processing and storage mechanisms
- **Execution Engine**: Core trading execution logic

### 2. Agent Types
- **Floor Agents**: Direct market interaction agents (arbitrage scanner, price watcher, risk gate)
- **Management Agents**: Strategic decision making (reflection agent)
- **Research Agents**: Market analysis and intelligence gathering (market analyst)

### 3. Compliance Layer
- **Compliance Officer**: Ensures all operations comply with regulations and internal policies

### 4. Monitoring & Alerting
- **Logger**: Comprehensive logging system for audit trails
- **Alerting**: Real-time notifications for critical events

## Agent Communication

### Orchestrator-Agent Relationship
The system operates with a clear hierarchy:
1. **Orchestrator (Claude)**: Makes high-level strategic decisions and coordinates agents
2. **Specialized Agents (Qwen)**: Handle specific tasks like data processing, API calls, or trading functions

### Communication Patterns
- **Synchronous**: Direct function calls for immediate responses
- **Asynchronous**: Event-driven communication for non-blocking operations
- **Message Queues**: For complex inter-agent communication

## Security Architecture

### Data Protection
- All secrets loaded from environment variables only
- Configuration files never committed to version control
- Hard limits in code prevent misconfiguration that could lead to losses

### Access Control
- Role-based access control for system components
- Audit logging for all operations
- Secure API endpoints with authentication

## Deployment Architecture

### Local Development
- Virtual environments for isolation
- Docker containerization for consistent deployment
- Local testing with mocked data sources

### Production
- Containerized deployment with orchestration
- Load balancing and auto-scaling
- Monitoring and alerting integration
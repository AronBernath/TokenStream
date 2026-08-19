import { Bot, Boxes, Database, Gauge, KeyRound, Network, Shield, Users } from "lucide-react";
import type { ComponentType } from "react";
import type { ViewId } from "./types";

export type NavItem = {
  id: ViewId;
  label: string;
  title: string;
  description: string;
  icon: ComponentType<{ size?: number }>;
};

export const navItems: NavItem[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    title: "Dashboard",
    description: "Runtime health, registry counts, and active retrieval configuration.",
    icon: Gauge
  },
  {
    id: "corpora",
    label: "Corpora",
    title: "Corpora & Sources",
    description: "Manage corpus records, source files, URL resources, and ingestion jobs.",
    icon: Boxes
  },
  {
    id: "providers",
    label: "Providers",
    title: "Providers",
    description: "LLM provider metadata and secret references used by orchestrator-api.",
    icon: Bot
  },
  {
    id: "policies",
    label: "Policies",
    title: "Policies",
    description: "Pipeline policies, model constraints, allowed tools, and corpus limits.",
    icon: Shield
  },
  {
    id: "keys",
    label: "Machine Keys",
    title: "Machine API Keys",
    description: "Programmatic keys for service-to-service and automation clients.",
    icon: KeyRound
  },
  {
    id: "users",
    label: "Users",
    title: "Users",
    description: "Human admin accounts, roles, permissions, and password rotation data.",
    icon: Users
  },
  {
    id: "rag",
    label: "RAG Settings",
    title: "RAG Settings",
    description: "Default corpus, selected corpora, top-k, and retrieval API routing.",
    icon: Database
  },
  {
    id: "mcp",
    label: "MCP Settings",
    title: "MCP Settings",
    description: "MCP server registry, selected servers, timeouts, and tool rounds.",
    icon: Network
  }
];

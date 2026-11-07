//! Syntax-only bridge. Never evaluates a document, imports, packages or resources.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, Read, Write};
use typst_syntax::{
    SyntaxNode,
    ast::{AstNode, MathAttach},
};

const MAX_INPUT: usize = 1_048_576;
const MAX_OUTPUT: usize = 16_777_216;
const MAX_NODES: usize = 100_000;
const MAX_DEPTH: usize = 256;

fn flatten(
    node: &SyntaxNode,
    start: usize,
    depth: usize,
    nodes: &mut Vec<Value>,
) -> Result<usize, String> {
    if depth > MAX_DEPTH || nodes.len() >= MAX_NODES {
        return Err("syntax tree resource limit exceeded".into());
    }
    let id = nodes.len();
    nodes.push(Value::Null);
    let mut children = Vec::new();
    let mut operands = Vec::new();
    let attach = node.cast::<MathAttach>();
    let mut offset = start;
    for child in node.children() {
        let child_id = flatten(child, offset, depth + 1, nodes)?;
        children.push(child_id);
        if let Some(attach) = attach {
            for (role, operand) in [("top", attach.top()), ("bottom", attach.bottom())] {
                if operand.is_some_and(|expr| std::ptr::eq(expr.to_untyped(), child)) {
                    operands.push(json!({"role": role, "node": child_id}));
                }
            }
        }
        offset += child.len();
    }
    nodes[id] = json!({"kind": format!("{:?}", node.kind()), "start": start,
                       "end": start + node.len(), "children": children,
                       "errors": node.diagnosis().errors, "operands": operands});
    Ok(id)
}

fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 1 || !["markup", "code", "math"].contains(&args[0].as_str()) {
        return Err("expected one mode: markup, code or math".into());
    }
    let mut raw = Vec::new();
    io::stdin()
        .take((MAX_INPUT + 1) as u64)
        .read_to_end(&mut raw)
        .map_err(|e| e.to_string())?;
    if raw.len() > MAX_INPUT {
        return Err("source exceeds 1 MiB limit".into());
    }
    let text = std::str::from_utf8(&raw).map_err(|_| "source must be UTF-8")?;
    let root = match args[0].as_str() {
        "markup" => typst_syntax::parse(text),
        "code" => typst_syntax::parse_code(text),
        "math" => typst_syntax::parse_math(text),
        _ => unreachable!(),
    };
    let mut nodes = Vec::new();
    flatten(&root, 0, 0, &mut nodes)?;
    let result = json!({"schema": 1, "adapter_version": "0.1.0", "syntax_version": "0.15.1",
                        "mode": args[0], "source_bytes": raw.len(),
                        "source_sha256": format!("{:x}", Sha256::digest(&raw)), "nodes": nodes});
    let output = serde_json::to_vec(&result).map_err(|e| e.to_string())?;
    if output.len() > MAX_OUTPUT {
        return Err("syntax output exceeds 16 MiB limit".into());
    }
    io::stdout().write_all(&output).map_err(|e| e.to_string())?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("typst-lint-syntax: {error}");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_utf8_attachment_spans() {
        let source = "$α^β$";
        let mut nodes = Vec::new();
        flatten(&typst_syntax::parse(source), 0, 0, &mut nodes).unwrap();
        let attach = nodes.iter().find(|n| n["kind"] == "MathAttach").unwrap();
        let operand = attach["operands"][0]["node"].as_u64().unwrap() as usize;
        let start = nodes[operand]["start"].as_u64().unwrap() as usize;
        let end = nodes[operand]["end"].as_u64().unwrap() as usize;
        assert_eq!(&source[start..end], "β");
        assert_eq!(nodes[0]["end"], source.len());
    }

    #[test]
    fn malformed_syntax_retains_error_nodes() {
        let mut nodes = Vec::new();
        flatten(&typst_syntax::parse("$x^$"), 0, 0, &mut nodes).unwrap();
        assert_eq!(nodes[0]["errors"], true);
        assert!(nodes.iter().any(|n| n["kind"] == "Error"));
    }

    #[test]
    fn bounded_traversal_refuses_depth_and_nodes() {
        let node = typst_syntax::parse("x");
        assert!(flatten(&node, 0, MAX_DEPTH + 1, &mut Vec::new()).is_err());
        assert!(flatten(&node, 0, 0, &mut vec![Value::Null; MAX_NODES]).is_err());
    }
}

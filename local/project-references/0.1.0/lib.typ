// Cross-project references to the published global reference catalog.
// The catalog is generated from the source project's reference rules.

#let project-ref(project, target) = {
  assert(
    type(project) == str,
    message: "project-ref: project must be a string alias, such as \"pde\"",
  )
  assert(
    type(target) == label,
    message: "project-ref: target must be a global label, such as <energy>",
  )

  let name = str(target)
  let local-prefixes = (
    "local-scope-",
    "local-scope-annotations-",
    "mannot-scope-",
    "_mannot-",
  )
  assert(
    not local-prefixes.any(prefix => name.starts-with(prefix)),
    message: "project-ref: local-scope labels cannot be referenced across projects",
  )

  let catalog = json("projects.json")
  assert(
    type(catalog) == dictionary
      and catalog.at("schema", default: none) == 1
      and type(catalog.at("projects", default: none)) == dictionary,
    message: "project-ref: invalid project catalog; export it again",
  )
  let projects = catalog.projects
  assert(
    project in projects,
    message: "project-ref: unknown project \"" + project + "\"; register and export the project first",
  )
  let source = projects.at(project)
  assert(
    name not in source.at("ambiguous_labels", default: ()),
    message: "project-ref: ambiguous global label <" + name + "> in project \"" + project + "\"; give each source target a unique label and export again",
  )
  assert(
    name in source.references,
    message: "project-ref: unknown global label <" + name + "> in project \"" + project + "\"; export the source project again",
  )
  let entry = source.references.at(name)

  link(
    source.pdf_url + "#page=" + str(entry.page),
    entry.text + " of " + source.title,
  )
}

from jinja2 import Environment, FileSystemLoader, select_autoescape


env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape()
)

template = env.get_template("software-title.html")


software_vars = {
    'icon': 'test-icon.png',
    'software_title': 'Some game',
    'year': 1995,
    'publisher': 'Foosoft',
    'author': 'nick, dave, steve',
    'categories': ['game', 'game-demo', 'platformer'],
    'screenshots': ['a.png', 'b.png']
}

print(template.render(software_vars))
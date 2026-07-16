To use a package, create the `.txt` inside this directory (or inside another directory inside of this folder). Then, inside your main file, declare an `import` statement:
```
import <path_to_package>;
```
Everything inside the package must have valid syntax. Code inside packages are parsed as if they were the main script. Your code may have only one entry point. If you define an entry point `main` inside one package, all other packages shouldn't have that method.

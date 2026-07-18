# Python-JDK
PJDK is an interesting and cool replica of the popular Java programming language by Oracle (originally by Sun Microsystems), created inside Python! (Also supports the C `unsigned` keyword since I think its important)

To play with P-JDK, the source code is presented inside source/PJDK.py. Download it, and it requires Python 3.12+ Intepreter to run. 

Of course, code made inside P-JDK are hundreds or even thousands of times slower than a real JDK. Obviously, this is not meant to be actually used, and rather a cool project.
One small advantage of this is that this skips the need of downloading a JDK or JRE and a JVM. This may be useful for quick tests, since is skips any use for `javac` to compile it, but directly imitates java. 

[Java SE Docs](https://docs.oracle.com/javase/8/docs/api/) was used to help create this

Since I was too lazy to create an entire AST for this, I used (smart + efficient) hard coded steps to evaluate statements. However, this may result in some weird parsing errors. Please make sure you include semicolons on the end of every line, since many expressions depend heavily on it to find the end of the statement. 

# Supports
P-JDK supports all basic Java syntax (and fully replicates it), like defining classes:
```
public class Main { }
```
Defining methods:
```
public int add(int x, int y) { }
```
Creating statements:
```
if (a == 1) { }
```
Calling methods:
```
bob.sayHello();
alice.sayHello();
```
And much more!

# How to Use
To create a file that is readable by PJDK, create a simple text document in the SAME directory as the executable. Then, upon running the PJDK, type in the name of this file. 

All files must have a valid entry point, like a standard Java program:
```
public class Main{
  public static void main(String[] args){

  }
}
```
Note: The entry class name is optional, and does not have to be the name of the file it is in (since this is not a real Java environment)

If you have difficulty creating the entry point, download a pre-made one from source/PJDK_Template.txt

# Contribute
If you find any bugs and want to report them, use the issue tracker. If you want to directly edit the code to push your own bug fixes, make a fork then a pull request. 

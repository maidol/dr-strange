package main

import "fmt"

func main() {
	svc := &Service{store: &AuditStore{inner: &MemStore{data: map[string]string{}}}}
	if err := svc.Save("a", " x "); err != nil {
		fmt.Println(err)
	}
	v, _ := svc.Load("a")
	fmt.Println(v, viaFuncValue(v), viaClosure(v))
}

// Package main is an accuracy fixture, not a program worth running.
//
// Every construct here is one a Go codebase actually uses, chosen because a
// parser that reads syntax alone has to decide what to do about it. The key
// beside this file says which calls are real; `callgraph` checks that claim.
package main

import "fmt"

// Store is the interface a handler depends on — the indirection that makes
// a call site's target ambiguous to a syntax-only parser.
type Store interface {
	Get(id string) (string, error)
	Put(id, value string) error
}

// MemStore is one implementation.
type MemStore struct{ data map[string]string }

func (m *MemStore) Get(id string) (string, error) {
	if v, ok := m.data[id]; ok {
		return v, nil
	}
	return "", fmt.Errorf("no such id %q", id)
}

func (m *MemStore) Put(id, value string) error {
	m.data[id] = value
	return nil
}

// AuditStore is a second implementation of the same interface, so an
// interface call has more than one possible target.
type AuditStore struct {
	inner Store
	log   []string
}

func (a *AuditStore) Get(id string) (string, error) {
	a.note("get " + id)
	return a.inner.Get(id)
}

func (a *AuditStore) Put(id, value string) error {
	a.note("put " + id)
	return a.inner.Put(id, value)
}

func (a *AuditStore) note(entry string) { a.log = append(a.log, entry) }
